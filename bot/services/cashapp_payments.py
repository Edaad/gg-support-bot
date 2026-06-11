"""Cash App payment ingest, Telegram notifications, and manual group binding."""

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
from bot.services.payment_method_binding import (
    BOUND_VIA_MANUAL_DASHBOARD,
    BOUND_VIA_MANUAL_NOTIFICATION,
    BOUND_VIA_MEMO_EMOJI,
    BOUND_VIA_SPECIAL_AMOUNT,
    _normalize_cashapp_handle,
    cancel_pending_attempts_for_chat_in_session,
    cancel_setup_attempt_in_session,
    complete_attempt_in_session,
    find_existing_cashapp_link_for_setup,
    get_last_bound_cashapp_deposit_at,
    infer_variant_id_for_cashapp_handle,
    match_pending_cashapp_setup_in_session,
    match_pending_memo_setup_in_session,
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
from db.models import CashAppPayerBinding, CashAppPayment
from notification.formatting import (
    format_group_chat_line,
    format_player_id_line,
    resolve_notification_linked_chat_id,
)

logger = logging.getLogger(__name__)

WEBHOOK_SECRET_ENV = "CASHAPP_ZAPIER_WEBHOOK_SECRET"


def normalize_cashapp_handle(handle: str) -> str:
    return _normalize_cashapp_handle(handle)


def _apply_binding_to_payment(
    payment: CashAppPayment,
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
    payment: CashAppPayment,
    *,
    already_bound_group_title: str,
    last_deposit_at: Optional[datetime],
    setup_chat_title: str,
) -> str:
    """Staff warning when a first-time setup payment matches an already-linked payer/group."""
    method = payment.cashapp_handle
    if not method.startswith("$"):
        method = f"${method.lstrip('$')}"

    last_deposit_line = (
        f"Last deposit: {_format_deposit_timestamp(last_deposit_at)}"
        if last_deposit_at is not None
        else "Last deposit: No prior bound deposits found"
    )

    lines = [
        "⚠️ First-time Cash App setup warning",
        "",
        f"Already bound: {escape_notification_html(already_bound_group_title)}",
    ]
    player_line = format_player_id_line(already_bound_group_title)
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
            f"Method: Cashapp ({escape_notification_html(method)})",
            f"Setup chat: {escape_notification_html(setup_chat_title)}",
        ]
    )

    body = "\n".join(lines)
    if getattr(payment, "is_test", False):
        return f"{TEST_NOTIFICATION_BANNER}\n\n{body}"
    return body


def format_notification_text(
    payment: CashAppPayment,
    *,
    group_title: Optional[str] = None,
    telegram_chat_id: Optional[int] = None,
    group_chat_url: Optional[str] = None,
) -> str:
    handle = payment.cashapp_handle
    if not handle.startswith("$"):
        handle = f"${handle.lstrip('$')}"

    lines = [
        "🔔 Cash App Payment Notification",
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
    lines.append(f"Method: Cashapp ({escape_notification_html(handle)})")

    body = "\n".join(lines)
    if getattr(payment, "is_test", False):
        return f"{TEST_NOTIFICATION_BANNER}\n\n{body}"
    return body


def _upsert_payer_binding(
    session,
    *,
    payer_name: str,
    cashapp_handle: str,
    telegram_chat_id: int,
    club_id: int,
    bound_group_title_at_bind: str,
    bound_by_telegram_user_id: Optional[int],
) -> None:
    normalized = normalize_payer_name(payer_name)
    handle = normalize_cashapp_handle(cashapp_handle)
    now = datetime.now(timezone.utc)
    row = (
        session.query(CashAppPayerBinding)
        .filter_by(payer_name_normalized=normalized)
        .one_or_none()
    )
    if row is None:
        row = CashAppPayerBinding(
            payer_name_normalized=normalized,
            cashapp_handle=handle,
        )
        session.add(row)
    row.cashapp_handle = handle
    row.telegram_chat_id = int(telegram_chat_id)
    row.club_id = int(club_id)
    row.bound_group_title_at_bind = bound_group_title_at_bind[:255]
    row.last_bound_at = now
    row.last_bound_by_telegram_user_id = bound_by_telegram_user_id


async def ingest_cashapp_payment(
    *,
    payer_name: str,
    amount: str | int | float | Decimal,
    cashapp_handle: str,
    paid_at: Optional[str] = None,
    source_external_id: Optional[str] = None,
    memo: Optional[str] = None,
    test: bool = False,
) -> IngestResult:
    """Create payment row, auto-bind if known payer, send Telegram notification."""
    payer = (payer_name or "").strip()
    if not payer:
        raise ValueError("payer_name is required")
    handle = normalize_cashapp_handle(cashapp_handle)
    if not handle:
        raise ValueError("cashapp_handle is required")
    amount_cents = parse_amount_cents(amount)
    memo_normalized = (memo or "").strip() or None
    source_id = (source_external_id or "").strip() or None

    logger.info(
        "cashapp ingest: processing payer=%r amount_cents=%s handle=%r "
        "memo_raw=%r memo_normalized=%r test=%s source_external_id=%r",
        payer,
        amount_cents,
        handle,
        memo,
        memo_normalized,
        test,
        source_id,
    )

    created = True
    auto_bound = False
    group_title: Optional[str] = None
    setup_warning_text: Optional[str] = None
    setup_blocked_already_linked = False
    setup_attempt_id: Optional[int] = None
    setup_bound_via: Optional[str] = None

    with get_db() as session:
        if source_id:
            existing = (
                session.query(CashAppPayment)
                .filter_by(source_external_id=source_id)
                .one_or_none()
            )
            if existing is not None:
                existing_memo = (existing.memo or "").strip() or None
                logger.info(
                    "cashapp ingest: dedup skip (no notification) source_external_id=%r "
                    "existing_payment_id=%s incoming_memo=%r stored_memo=%r "
                    "incoming_test=%s stored_is_test=%s",
                    source_id,
                    existing.id,
                    memo_normalized,
                    existing_memo,
                    test,
                    bool(existing.is_test),
                )
                return IngestResult(
                    payment_id=int(existing.id),
                    status="bound" if existing.telegram_chat_id else "unbound",
                    auto_bound=bool(existing.auto_bound),
                    created=False,
                )

        payment = CashAppPayment(
            payer_name=payer,
            amount_cents=amount_cents,
            cashapp_handle=handle,
            paid_at=(paid_at or "").strip() or None,
            source_external_id=source_id,
            memo=memo_normalized,
            is_test=bool(test),
        )
        session.add(payment)
        session.flush()
        logger.info(
            "cashapp ingest: row created payment_id=%s stored_memo=%r is_test=%s",
            payment.id,
            payment.memo,
            payment.is_test,
        )

        setup_attempt = match_pending_memo_setup_in_session(
            session,
            payment_method_slug="cashapp",
            cashapp_handle=handle,
            memo=memo,
        )
        setup_bound_via = BOUND_VIA_MEMO_EMOJI
        if setup_attempt is None:
            setup_bound_via = BOUND_VIA_SPECIAL_AMOUNT
            setup_attempt = match_pending_cashapp_setup_in_session(
                session,
                amount_cents=amount_cents,
                cashapp_handle=handle,
            )
        if setup_attempt is not None:
            setup_attempt_id = int(setup_attempt.id)
            live_title = resolve_display_group_title(int(setup_attempt.telegram_chat_id))
            club_id_setup = int(setup_attempt.club_id)
            if not live_title:
                logger.warning(
                    "cashapp ingest: setup attempt matched but group title missing "
                    "attempt_id=%s chat_id=%s payment_id=%s handle=%r memo=%r",
                    setup_attempt.id,
                    setup_attempt.telegram_chat_id,
                    payment.id,
                    handle,
                    memo,
                )
            if live_title:
                existing_link = find_existing_cashapp_link_for_setup(
                    session,
                    payer_name=payer,
                    setup_chat_id=int(setup_attempt.telegram_chat_id),
                )
                if existing_link is not None:
                    linked_title = (
                        resolve_display_group_title(int(existing_link.linked_chat_id))
                        or "—"
                    )
                    last_deposit_at = get_last_bound_cashapp_deposit_at(
                        session,
                        payer_name=payer,
                        telegram_chat_id=int(existing_link.linked_chat_id),
                        exclude_payment_id=int(payment.id),
                    )
                    cancel_setup_attempt_in_session(session, setup_attempt)
                    setup_blocked_already_linked = True
                    setup_warning_text = format_setup_already_linked_warning(
                        payment,
                        already_bound_group_title=linked_title,
                        last_deposit_at=last_deposit_at,
                        setup_chat_title=live_title,
                    )
                elif complete_attempt_in_session(
                    session,
                    setup_attempt,
                    cashapp_payment_id=int(payment.id),
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
                        cashapp_handle=payment.cashapp_handle,
                        telegram_chat_id=int(setup_attempt.telegram_chat_id),
                        club_id=club_id_setup,
                        bound_group_title_at_bind=live_title,
                        bound_by_telegram_user_id=None,
                    )
                    record_group_binding_in_session(
                        session,
                        telegram_chat_id=int(setup_attempt.telegram_chat_id),
                        club_id=club_id_setup,
                        payment_method_slug="cashapp",
                        bound_via=setup_bound_via,
                        variant_id=int(setup_attempt.variant_id),
                        venmo_handle=handle,
                        first_bind_attempt_id=int(setup_attempt.id),
                    )
                    logger.info(
                        "cashapp ingest: setup bind matched attempt_id=%s payment_id=%s "
                        "chat_id=%s via=%s",
                        setup_attempt.id,
                        payment.id,
                        setup_attempt.telegram_chat_id,
                        setup_bound_via,
                    )

        if not auto_bound and not setup_blocked_already_linked:
            binding = (
                session.query(CashAppPayerBinding)
                .filter_by(payer_name_normalized=normalize_payer_name(payer))
                .one_or_none()
            )
        else:
            binding = None

        if binding is not None:
            live_title = resolve_display_group_title(int(binding.telegram_chat_id))
            club_id = binding.club_id
            if club_id is None:
                _t, club_id = get_group_title_for_chat(int(binding.telegram_chat_id))
            if live_title and club_id is not None:
                auto_bound = True
                group_title = live_title
                _apply_binding_to_payment(
                    payment,
                    telegram_chat_id=int(binding.telegram_chat_id),
                    club_id=int(club_id),
                    bound_group_title_at_bind=live_title,
                    auto_bound=True,
                )

        payment_id = int(payment.id)
        session.flush()
        session.expunge(payment)

    group_chat_url = await resolve_group_chat_url_for_payment(
        payment,
        group_title=group_title,
    )
    text = format_notification_text(
        payment,
        group_title=group_title,
        group_chat_url=group_chat_url,
    )

    if setup_warning_text:
        await send_telegram_notification(setup_warning_text)

    notif_chat_id, notif_message_id = await send_telegram_notification(text)

    with get_db() as session:
        payment = session.query(CashAppPayment).filter_by(id=payment_id).one()
        payment.notification_chat_id = notif_chat_id
        payment.notification_message_id = notif_message_id
        bound_chat_id = payment.telegram_chat_id
        bound_club_id = payment.club_id
        bound_title = payment.bound_group_title_at_bind

    track_ingest_notification(
        payment_method_slug="cashapp",
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

    status = "bound" if auto_bound else "unbound"
    memo_in_notification = "Memo:" in text
    logger.info(
        "cashapp ingest: notification sent payment_id=%s payer=%r amount_cents=%s "
        "auto_bound=%s memo_in_notification=%s notification_chat_id=%s",
        payment_id,
        payer,
        amount_cents,
        auto_bound,
        memo_in_notification,
        notif_chat_id,
    )
    return IngestResult(
        payment_id=payment_id,
        status=status,
        auto_bound=auto_bound,
        created=created,
    )


async def bind_cashapp_payment_by_id(
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
        payment = (
            session.query(CashAppPayment).filter_by(id=int(payment_id)).one_or_none()
        )
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
            cashapp_handle=payment.cashapp_handle,
            telegram_chat_id=group.telegram_chat_id,
            club_id=group.club_id,
            bound_group_title_at_bind=group.group_title,
            bound_by_telegram_user_id=bound_by_telegram_user_id,
        )

        variant_id = infer_variant_id_for_cashapp_handle(
            int(group.club_id),
            payment.cashapp_handle,
        )
        cancel_pending_attempts_for_chat_in_session(
            session,
            telegram_chat_id=int(group.telegram_chat_id),
            payment_method_slug="cashapp",
        )
        record_group_binding_in_session(
            session,
            telegram_chat_id=int(group.telegram_chat_id),
            club_id=int(group.club_id),
            payment_method_slug="cashapp",
            bound_via=bound_via,
            variant_id=variant_id,
            venmo_handle=payment.cashapp_handle,
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
        payment_method_slug="cashapp",
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
            await sync_payment_notification_edit(
                payment_method_slug="cashapp",
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
            )
        except Exception:
            logger.exception(
                "cashapp bind: notification edit failed payment_id=%s chat_id=%s message_id=%s",
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


async def bind_cashapp_payment_from_reply(
    *,
    notification_chat_id: int,
    notification_message_id: int,
    group_title_input: str,
    bound_by_telegram_user_id: int,
) -> BindResult:
    """Bind or rebind a payment from a reply in the notification group."""
    with get_db() as session:
        payment = (
            session.query(CashAppPayment)
            .filter_by(
                notification_chat_id=int(notification_chat_id),
                notification_message_id=int(notification_message_id),
            )
            .one_or_none()
        )
        if payment is None:
            return BindResult(ok=False, error="No payment found for this notification.")
        payment_id = int(payment.id)

    return await bind_cashapp_payment_by_id(
        payment_id=payment_id,
        group_title_input=group_title_input,
        bound_by_telegram_user_id=int(bound_by_telegram_user_id),
        bound_via=BOUND_VIA_MANUAL_NOTIFICATION,
    )
