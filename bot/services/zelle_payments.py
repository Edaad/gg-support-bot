"""Zelle payment ingest, Telegram notifications, and manual group binding."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from bot.services.club import get_group_title_for_chat
from bot.services.group_chat_invite_links import resolve_group_chat_url_for_payment
from bot.services.payment_binding_events import (
    record_payment_bound,
    sync_payment_notification_edit,
    track_ingest_notification,
)
from bot.services.payment_group_notify import maybe_notify_player_on_auto_bound
from bot.services.payment_method_binding import (
    BOUND_VIA_MANUAL_DASHBOARD,
    BOUND_VIA_MANUAL_NOTIFICATION,
    BOUND_VIA_SPECIAL_AMOUNT,
    cancel_pending_attempts_for_chat_in_session,
    cancel_setup_attempt_in_session,
    complete_attempt_in_session,
    find_existing_zelle_link_for_setup,
    get_last_bound_zelle_deposit_at,
    infer_variant_id_for_zelle_recipient,
    match_pending_zelle_setup_in_session,
    normalize_zelle_recipient,
    record_group_binding_in_session,
)
from bot.services.venmo_payments import (
    BindResult,
    BoundGroup,
    IngestResult,
    escape_notification_html,
    format_amount_display,
    normalize_payer_name,
    parse_amount_cents,
    resolve_bound_group,
    resolve_display_group_title,
    send_telegram_notification,
    TEST_NOTIFICATION_BANNER,
    _format_deposit_timestamp,
)
from db.connection import get_db
from db.models import ZellePayerBinding, ZellePayment
from notification.formatting import (
    format_group_chat_line,
    format_player_id_line,
    resolve_notification_linked_chat_id,
)

logger = logging.getLogger(__name__)

WEBHOOK_SECRET_ENV = "ZELLE_ZAPIER_WEBHOOK_SECRET"


def _apply_binding_to_payment(
    payment: ZellePayment,
    *,
    telegram_chat_id: int,
    club_id: int,
    bound_group_title_at_bind: str,
    auto_bound: bool,
    bound_by_telegram_user_id: Optional[int] = None,
) -> None:
    now = datetime.now(timezone.utc)
    payment.telegram_chat_id = int(telegram_chat_id)
    payment.club_id = int(club_id)
    payment.bound_group_title_at_bind = bound_group_title_at_bind[:255]
    payment.auto_bound = auto_bound
    payment.bound_at = now
    payment.bound_by_telegram_user_id = bound_by_telegram_user_id


def format_setup_already_linked_warning(
    payment: ZellePayment,
    *,
    already_bound_group_title: str,
    already_bound_group_titles: list[str] | None = None,
    last_deposit_at: Optional[datetime],
    setup_chat_title: str,
) -> str:
    """Staff warning when a first-time setup payment matches an already-linked payer/group."""
    from notification.formatting import format_setup_blocked_header_lines

    titles = already_bound_group_titles or [already_bound_group_title]
    titles = [t for t in titles if (t or "").strip()] or [already_bound_group_title]

    last_deposit_line = (
        f"Last deposit: {_format_deposit_timestamp(last_deposit_at)}"
        if last_deposit_at is not None
        else "Last deposit: No prior bound deposits found"
    )

    lines = format_setup_blocked_header_lines(
        single_title_preamble="⚠️ First-time Zelle setup warning",
        multi_candidate=len(titles) > 1,
    )
    lines.extend(
        [
            f"Auto-bind attempt from {escape_notification_html(setup_chat_title)} blocked.",
            "Payer name already has possible match(es):",
        ]
    )
    for title in titles:
        lines.append(f"• {escape_notification_html(title)}")
    player_line = format_player_id_line(titles[0])
    if player_line:
        lines.append(player_line)
    lines.extend(
        [
            last_deposit_line,
            "",
            "Incoming setup payment matched but was left unbound for manual review.",
            f"Name: {escape_notification_html(payment.payer_name)}",
            f"Amount: {format_amount_display(payment.amount_cents, bold=True)}",
        ]
    )
    memo = (getattr(payment, "memo", None) or "").strip()
    if memo:
        lines.append(f"Memo: {escape_notification_html(memo)}")
    lines.extend(
        [
            f"Method: {escape_notification_html(payment.zelle_recipient)}",
            f"Setup chat: {escape_notification_html(setup_chat_title)}",
        ]
    )

    body = "\n".join(lines)
    if getattr(payment, "is_test", False):
        return f"{TEST_NOTIFICATION_BANNER}\n\n{body}"
    return body


def format_notification_text(
    payment: ZellePayment,
    *,
    group_title: Optional[str] = None,
    telegram_chat_id: Optional[int] = None,
    group_chat_url: Optional[str] = None,
) -> str:
    lines = [
        "🔔 Zelle Payment Notification",
        "",
        format_group_chat_line(
            group_title=group_title,
            telegram_chat_id=resolve_notification_linked_chat_id(
                payment,
                telegram_chat_id=telegram_chat_id,
            ),
            group_chat_url=group_chat_url,
        ),
    ]
    player_line = format_player_id_line(group_title)
    if player_line:
        lines.append(player_line)
    lines.extend(
        [
            "",
            f"Name: {escape_notification_html(payment.payer_name)}",
            f"Amount: {format_amount_display(payment.amount_cents, bold=True)}",
        ]
    )
    memo = (getattr(payment, "memo", None) or "").strip()
    if memo:
        lines.append(f"Memo: {escape_notification_html(memo)}")
    lines.append(f"Method: {escape_notification_html(payment.zelle_recipient)}")

    body = "\n".join(lines)
    if getattr(payment, "is_test", False):
        return f"{TEST_NOTIFICATION_BANNER}\n\n{body}"
    return body


def _upsert_payer_binding(
    session,
    *,
    payer_name: str,
    zelle_recipient: str,
    telegram_chat_id: int,
    club_id: int,
    bound_group_title_at_bind: str,
    bound_by_telegram_user_id: Optional[int],
) -> None:
    from bot.services.payment_bind_candidates import upsert_candidate_on_bind

    upsert_candidate_on_bind(
        session,
        "zelle",
        payer_name=payer_name,
        method_handle=zelle_recipient,
        telegram_chat_id=telegram_chat_id,
        club_id=club_id,
        bound_group_title_at_bind=bound_group_title_at_bind,
        bound_by_telegram_user_id=bound_by_telegram_user_id,
    )


def find_zelle_payment_by_notification_message(
    notification_chat_id: int,
    notification_message_id: int,
) -> Optional[ZellePayment]:
    with get_db() as session:
        return (
            session.query(ZellePayment)
            .filter_by(
                notification_chat_id=int(notification_chat_id),
                notification_message_id=int(notification_message_id),
            )
            .one_or_none()
        )


async def ingest_zelle_payment(
    *,
    payer_name: str,
    amount: str | int | float | Decimal,
    zelle_recipient: str,
    paid_at: Optional[str] = None,
    source_external_id: Optional[str] = None,
    memo: Optional[str] = None,
    test: bool = False,
) -> IngestResult:
    """Create payment row, auto-bind if known payer, send Telegram notification."""
    payer = (payer_name or "").strip()
    if not payer:
        raise ValueError("payer_name is required")
    recipient = normalize_zelle_recipient(zelle_recipient)
    if not recipient:
        raise ValueError("zelle_recipient is required")
    amount_cents = parse_amount_cents(amount)

    created = True
    auto_bound = False
    group_title: Optional[str] = None
    setup_warning_text: Optional[str] = None
    setup_blocked_already_linked = False
    setup_attempt_id: Optional[int] = None
    setup_bound_via: Optional[str] = None
    ambiguous_candidates: list = []
    setup_target_chat_id: Optional[int] = None
    setup_target_title: Optional[str] = None

    with get_db() as session:
        if source_external_id:
            existing = (
                session.query(ZellePayment)
                .filter_by(source_external_id=source_external_id.strip())
                .one_or_none()
            )
            if existing is not None:
                logger.info(
                    "zelle ingest: idempotent reject source_external_id=%r "
                    "existing_payment_id=%s",
                    source_external_id.strip(),
                    existing.id,
                )
                return IngestResult(
                    payment_id=int(existing.id),
                    status="bound" if existing.telegram_chat_id else "unbound",
                    auto_bound=bool(existing.auto_bound),
                    created=False,
                )

        payment = ZellePayment(
            payer_name=payer,
            amount_cents=amount_cents,
            zelle_recipient=recipient,
            paid_at=(paid_at or "").strip() or None,
            source_external_id=(source_external_id or "").strip() or None,
            memo=(memo or "").strip() or None,
            is_test=bool(test),
        )
        session.add(payment)
        session.flush()

        setup_attempt = match_pending_zelle_setup_in_session(
            session,
            amount_cents=amount_cents,
            zelle_recipient=recipient,
        )
        setup_bound_via = BOUND_VIA_SPECIAL_AMOUNT
        if setup_attempt is not None:
            setup_attempt_id = int(setup_attempt.id)
            live_title = resolve_display_group_title(int(setup_attempt.telegram_chat_id))
            club_id_setup = int(setup_attempt.club_id)
            if live_title:
                existing_link = find_existing_zelle_link_for_setup(
                    session,
                    payer_name=payer,
                    setup_chat_id=int(setup_attempt.telegram_chat_id),
                )
                if existing_link is not None:
                    linked_titles = [
                        resolve_display_group_title(int(cid)) or "—"
                        for cid in existing_link.linked_chat_ids
                    ]
                    linked_title = linked_titles[0] if linked_titles else "—"
                    last_deposit_at = get_last_bound_zelle_deposit_at(
                        session,
                        payer_name=payer,
                        telegram_chat_id=int(existing_link.linked_chat_ids[0]),
                        exclude_payment_id=int(payment.id),
                    )
                    cancel_setup_attempt_in_session(session, setup_attempt)
                    setup_blocked_already_linked = True
                    setup_target_chat_id = int(setup_attempt.telegram_chat_id)
                    setup_target_title = live_title
                    setup_warning_text = format_setup_already_linked_warning(
                        payment,
                        already_bound_group_title=linked_title,
                        already_bound_group_titles=linked_titles,
                        last_deposit_at=last_deposit_at,
                        setup_chat_title=live_title,
                    )
                elif complete_attempt_in_session(
                    session,
                    setup_attempt,
                    zelle_payment_id=int(payment.id),
                ):
                    auto_bound = True
                    group_title = live_title
                    _apply_binding_to_payment(
                        payment,
                        telegram_chat_id=int(setup_attempt.telegram_chat_id),
                        club_id=club_id_setup,
                        bound_group_title_at_bind=live_title,
                        auto_bound=True,
                    )
                    _upsert_payer_binding(
                        session,
                        payer_name=payment.payer_name,
                        zelle_recipient=payment.zelle_recipient,
                        telegram_chat_id=int(setup_attempt.telegram_chat_id),
                        club_id=club_id_setup,
                        bound_group_title_at_bind=live_title,
                        bound_by_telegram_user_id=None,
                    )
                    record_group_binding_in_session(
                        session,
                        telegram_chat_id=int(setup_attempt.telegram_chat_id),
                        club_id=club_id_setup,
                        payment_method_slug="zelle",
                        bound_via=setup_bound_via,
                        variant_id=int(setup_attempt.variant_id),
                        venmo_handle=recipient,
                        first_bind_attempt_id=int(setup_attempt.id),
                    )
                    logger.info(
                        "zelle ingest: setup bind matched attempt_id=%s payment_id=%s",
                        setup_attempt.id,
                        payment.id,
                    )

        if not auto_bound and not setup_blocked_already_linked:
            from bot.services.payment_bind_candidates import candidates_for_payment
            from notification.payment_bind_helpers import auto_bind_from_candidates

            ambiguous_candidates = candidates_for_payment(session, payment, "zelle")
            single = auto_bind_from_candidates(ambiguous_candidates)
            if single is not None:
                auto_bound = True
                group_title = single.group_title
                _apply_binding_to_payment(
                    payment,
                    telegram_chat_id=int(single.telegram_chat_id),
                    club_id=int(single.club_id),
                    bound_group_title_at_bind=single.group_title,
                    auto_bound=True,
                )
            elif len(ambiguous_candidates) > 1:
                ambiguous_candidates = list(ambiguous_candidates)
            else:
                ambiguous_candidates = []

        payment_id = int(payment.id)
        session.flush()
        session.expunge(payment)

    from notification.bind_keyboards import candidate_picker_markup, setup_blocked_markup
    from notification.payment_bind_helpers import format_payment_notification

    group_chat_url = await resolve_group_chat_url_for_payment(
        payment,
        group_title=group_title,
    )
    text = format_payment_notification(
        "zelle",
        payment,
        group_title=group_title,
        group_chat_url=group_chat_url,
        ambiguous_candidates=ambiguous_candidates if not auto_bound else None,
    )

    notif_markup: dict | None = None
    if setup_blocked_already_linked and setup_target_chat_id and setup_target_title:
        from bot.services.payment_bind_candidates import candidate_chat_ids

        with get_db() as session:
            existing_ids = candidate_chat_ids(session, "zelle", payer_name=payment.payer_name)
        notif_markup = setup_blocked_markup(
            "zelle",
            int(payment.id),
            setup_chat_id=int(setup_target_chat_id),
            setup_title=setup_target_title,
            show_add=int(setup_target_chat_id) not in existing_ids,
        )
    elif ambiguous_candidates and len(ambiguous_candidates) > 1 and not auto_bound:
        notif_markup = candidate_picker_markup("zelle", int(payment.id), ambiguous_candidates)

    if setup_warning_text:
        await send_telegram_notification(setup_warning_text)

    notif_chat_id, notif_message_id = await send_telegram_notification(
        text,
        reply_markup=notif_markup,
    )

    from bot.services.payment_bind_candidates import identity_label
    from notification.payment_bind_helpers import log_ingest_bind_delivery

    log_ingest_bind_delivery(
        method_slug="zelle",
        payment_id=payment_id,
        identity_label=identity_label("zelle", payer_name=payment.payer_name),
        candidate_count=1 if auto_bound else len(ambiguous_candidates),
        auto_bound=auto_bound,
        bound_chat_id=payment.telegram_chat_id,
        bound_title=group_title,
        setup_blocked=setup_blocked_already_linked,
        setup_target_chat_id=setup_target_chat_id,
        notif_markup=notif_markup,
        notification_chat_id=notif_chat_id,
        notification_message_id=notif_message_id,
    )

    with get_db() as session:
        payment = session.query(ZellePayment).filter_by(id=payment_id).one()
        payment.notification_chat_id = notif_chat_id
        payment.notification_message_id = notif_message_id
        bound_chat_id = payment.telegram_chat_id
        bound_club_id = payment.club_id
        bound_title = payment.bound_group_title_at_bind

    track_ingest_notification(
        payment_method_slug="zelle",
        payment_id=payment_id,
        notification_chat_id=notif_chat_id,
        notification_message_id=notif_message_id,
        telegram_chat_id=int(bound_chat_id) if bound_chat_id is not None else None,
        club_id=int(bound_club_id) if bound_club_id is not None else None,
        bound_group_title=bound_title or group_title,
        auto_bound=auto_bound,
        bound_via=setup_bound_via,
        bind_attempt_id=setup_attempt_id,
    )
    await maybe_notify_player_on_auto_bound(
        telegram_chat_id=bound_chat_id,
        amount_cents=amount_cents,
        auto_bound=auto_bound,
        is_test=bool(test),
    )

    from bot.services.payment_auto_deposit import schedule_auto_deposit_from_payment

    schedule_auto_deposit_from_payment(
        club_id=int(bound_club_id) if bound_club_id is not None else None,
        telegram_chat_id=int(bound_chat_id) if bound_chat_id is not None else None,
        amount_cents=amount_cents,
        auto_bound=auto_bound,
        payment_method_slug="zelle",
        payment_id=payment_id,
        group_title=bound_title or group_title,
    )

    status = "bound" if auto_bound else "unbound"
    logger.info(
        "zelle payment ingested id=%s payer=%r amount_cents=%s auto_bound=%s",
        payment_id,
        payer,
        amount_cents,
        auto_bound,
    )
    return IngestResult(
        payment_id=payment_id,
        status=status,
        auto_bound=auto_bound,
        created=created,
    )


async def bind_zelle_payment_by_id(
    *,
    payment_id: int,
    group_title_input: str,
    bound_by_telegram_user_id: Optional[int] = None,
    bound_via: str = BOUND_VIA_MANUAL_DASHBOARD,
) -> BindResult:
    """Bind or rebind a payment to a support group by payment id."""
    result = resolve_bound_group(group_title_input)
    if not result.ok or result.bound_group is None:
        return result

    group = result.bound_group
    notif_chat_id: Optional[int] = None
    notif_message_id: Optional[int] = None
    text: Optional[str] = None
    live_title = group.group_title
    previous_telegram_chat_id: Optional[int] = None

    with get_db() as session:
        payment = session.query(ZellePayment).filter_by(id=int(payment_id)).one_or_none()
        if payment is None:
            return BindResult(ok=False, error="Payment not found.")

        previous_telegram_chat_id = payment.telegram_chat_id

        _apply_binding_to_payment(
            payment,
            telegram_chat_id=group.telegram_chat_id,
            club_id=group.club_id,
            bound_group_title_at_bind=group.group_title,
            auto_bound=False,
            bound_by_telegram_user_id=bound_by_telegram_user_id,
        )
        _upsert_payer_binding(
            session,
            payer_name=payment.payer_name,
            zelle_recipient=payment.zelle_recipient,
            telegram_chat_id=group.telegram_chat_id,
            club_id=group.club_id,
            bound_group_title_at_bind=group.group_title,
            bound_by_telegram_user_id=bound_by_telegram_user_id,
        )

        variant_id = infer_variant_id_for_zelle_recipient(
            int(group.club_id),
            payment.zelle_recipient,
        )
        cancel_pending_attempts_for_chat_in_session(
            session,
            telegram_chat_id=int(group.telegram_chat_id),
            payment_method_slug="zelle",
        )
        record_group_binding_in_session(
            session,
            telegram_chat_id=int(group.telegram_chat_id),
            club_id=int(group.club_id),
            payment_method_slug="zelle",
            bound_via=bound_via,
            variant_id=variant_id,
            venmo_handle=payment.zelle_recipient,
            bound_by_telegram_user_id=bound_by_telegram_user_id,
        )

        live_title = resolve_display_group_title(group.telegram_chat_id) or group.group_title
        if payment.notification_chat_id and payment.notification_message_id:
            notif_chat_id = int(payment.notification_chat_id)
            notif_message_id = int(payment.notification_message_id)
        session.flush()
        session.expunge(payment)

    if notif_chat_id and notif_message_id:
        group_chat_url = await resolve_group_chat_url_for_payment(
            payment,
            group_title=live_title,
        )
        text = format_notification_text(
            payment,
            group_title=live_title,
            group_chat_url=group_chat_url,
        )

    record_payment_bound(
        payment_method_slug="zelle",
        payment_id=payment_id,
        telegram_chat_id=group.telegram_chat_id,
        club_id=group.club_id,
        bound_group_title=live_title,
        bound_via=bound_via,
        auto_bound=False,
        actor_telegram_user_id=bound_by_telegram_user_id,
        notification_chat_id=notif_chat_id,
        notification_message_id=notif_message_id,
        previous_telegram_chat_id=int(previous_telegram_chat_id)
        if previous_telegram_chat_id is not None
        else None,
    )

    if notif_chat_id and notif_message_id and text:
        try:
            from notification.bind_keyboards import empty_markup

            await sync_payment_notification_edit(
                payment_method_slug="zelle",
                payment_id=payment_id,
                notification_chat_id=notif_chat_id,
                notification_message_id=notif_message_id,
                text=text,
                bound_via=bound_via,
                actor_telegram_user_id=bound_by_telegram_user_id,
                telegram_chat_id=group.telegram_chat_id,
                club_id=group.club_id,
                bound_group_title=live_title,
                auto_bound=False,
                reply_markup=empty_markup(),
            )
        except Exception:
            logger.exception(
                "zelle bind: notification edit failed payment_id=%s chat_id=%s message_id=%s",
                payment_id,
                notif_chat_id,
                notif_message_id,
            )

    return BindResult(
        ok=True,
        bound_group=BoundGroup(
            telegram_chat_id=group.telegram_chat_id,
            club_id=group.club_id,
            group_title=live_title,
        ),
    )


async def bind_zelle_payment_from_reply(
    *,
    notification_chat_id: int,
    notification_message_id: int,
    group_title_input: str,
    bound_by_telegram_user_id: int,
) -> BindResult:
    """Bind or rebind a payment from a reply in the notification group."""
    with get_db() as session:
        payment = (
            session.query(ZellePayment)
            .filter_by(
                notification_chat_id=int(notification_chat_id),
                notification_message_id=int(notification_message_id),
            )
            .one_or_none()
        )
        if payment is None:
            return BindResult(ok=False, error="No payment found for this notification.")
        payment_id = int(payment.id)

    return await bind_zelle_payment_by_id(
        payment_id=payment_id,
        group_title_input=group_title_input,
        bound_by_telegram_user_id=int(bound_by_telegram_user_id),
        bound_via=BOUND_VIA_MANUAL_NOTIFICATION,
    )
