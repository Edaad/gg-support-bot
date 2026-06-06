"""Venmo payment ingest, Telegram notifications, and manual group binding."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

import httpx

from bot.services.club import find_group_chat_id_by_name, get_group_title_for_chat
from bot.services.player_details import (
    parse_tracking_title,
    resolve_club_id_from_shorthand,
)
from db.connection import get_db
from bot.services.payment_method_binding import (
    BOUND_VIA_MANUAL_DASHBOARD,
    BOUND_VIA_MANUAL_NOTIFICATION,
    BOUND_VIA_MEMO_EMOJI,
    BOUND_VIA_SPECIAL_AMOUNT,
    cancel_pending_attempts_for_chat_in_session,
    cancel_setup_attempt_in_session,
    complete_attempt_in_session,
    find_existing_venmo_link_for_setup,
    get_last_bound_deposit_at,
    infer_variant_id_for_venmo_handle,
    match_pending_memo_setup_in_session,
    match_pending_venmo_setup_in_session,
    record_group_binding_in_session,
)
from db.models import VenmoPayerBinding, VenmoPayment

logger = logging.getLogger(__name__)

WEBHOOK_SECRET_ENV = "VENMO_ZAPIER_WEBHOOK_SECRET"
from notification.constants import (
    NOTIFICATION_BOT_TOKEN_ENV,
    PAYMENT_NOTIFICATION_CHAT_ID_ENV,
    debug_notification_enabled,
)

_AMOUNT_RE = re.compile(r"[^\d.]")


@dataclass(frozen=True)
class BoundGroup:
    telegram_chat_id: int
    club_id: int
    group_title: str


@dataclass(frozen=True)
class BindResult:
    ok: bool
    error: Optional[str] = None
    bound_group: Optional[BoundGroup] = None


@dataclass(frozen=True)
class IngestResult:
    payment_id: int
    status: str
    auto_bound: bool
    created: bool


def normalize_payer_name(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def normalize_venmo_handle(handle: str) -> str:
    raw = (handle or "").strip()
    if not raw:
        return raw
    if not raw.startswith("@"):
        raw = f"@{raw}"
    return raw.lower()


def parse_amount_cents(amount: str | int | float | Decimal) -> int:
    if isinstance(amount, int):
        return int(amount) * 100
    if isinstance(amount, float):
        return int(round(amount * 100))
    if isinstance(amount, Decimal):
        return int((amount * 100).quantize(Decimal("1")))
    text = str(amount or "").strip()
    if not text:
        raise ValueError("amount is required")
    cleaned = _AMOUNT_RE.sub("", text)
    if not cleaned:
        raise ValueError(f"invalid amount: {amount!r}")
    try:
        dollars = Decimal(cleaned)
    except InvalidOperation as e:
        raise ValueError(f"invalid amount: {amount!r}") from e
    return int((dollars * 100).quantize(Decimal("1")))


def format_amount_display(amount_cents: int) -> str:
    dollars = int(
        (Decimal(amount_cents) / Decimal(100)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )
    return f"${dollars:,}"


def resolve_display_group_title(chat_id: int) -> Optional[str]:
    title, _club_id = get_group_title_for_chat(int(chat_id))
    if title and title.strip():
        return title.strip()
    return None


def resolve_bound_group(title: str) -> BindResult:
    """Resolve a group title string to a linked support group."""
    cleaned = (title or "").strip()
    if not cleaned:
        return BindResult(ok=False, error="Group title is empty.")

    parsed = parse_tracking_title(cleaned)
    if not parsed:
        return BindResult(
            ok=False,
            error=(
                "Invalid group title format. Use: CLUB / PLAYER_ID / NAME "
                "(e.g. RT / 6485-8168 / Angus Mcgoon)."
            ),
        )

    shorthand, _gg_player_id = parsed
    club_id = resolve_club_id_from_shorthand(shorthand)
    if club_id is None:
        return BindResult(ok=False, error=f"Unknown club shorthand: {shorthand!r}.")

    chat_id = find_group_chat_id_by_name(int(club_id), cleaned)
    if chat_id is None:
        return BindResult(
            ok=False,
            error=f"No linked group found with title:\n{cleaned}",
        )

    live_title = resolve_display_group_title(int(chat_id)) or cleaned
    return BindResult(
        ok=True,
        bound_group=BoundGroup(
            telegram_chat_id=int(chat_id),
            club_id=int(club_id),
            group_title=live_title,
        ),
    )


def _notification_chat_id() -> Optional[int]:
    raw = (os.getenv(PAYMENT_NOTIFICATION_CHAT_ID_ENV) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid %s=%r", PAYMENT_NOTIFICATION_CHAT_ID_ENV, raw)
        return None


def _notification_bot_token() -> Optional[str]:
    return (os.getenv(NOTIFICATION_BOT_TOKEN_ENV) or "").strip() or None


TEST_NOTIFICATION_BANNER = "TEST (Please ignore)"


def _format_deposit_timestamp(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%b %d, %Y %I:%M %p UTC")


def format_setup_already_linked_warning(
    payment: VenmoPayment,
    *,
    already_bound_group_title: str,
    last_deposit_at: Optional[datetime],
    setup_chat_title: str,
) -> str:
    """Staff warning when a first-time setup payment matches an already-linked payer/group."""
    method = payment.venmo_handle
    if not method.startswith("@"):
        method = f"@{method.lstrip('@')}"

    last_deposit_line = (
        f"Last deposit: {_format_deposit_timestamp(last_deposit_at)}"
        if last_deposit_at is not None
        else "Last deposit: No prior bound deposits found"
    )

    lines = [
        "⚠️ First-time setup warning",
        "",
        f"Already bound: {already_bound_group_title}",
        last_deposit_line,
        "",
        "Incoming setup payment matched but was left unbound for manual review.",
        f"Name: {payment.payer_name}",
        f"Amount: {format_amount_display(payment.amount_cents)}",
    ]
    memo = (getattr(payment, "memo", None) or "").strip()
    if memo:
        lines.append(f"Memo: {memo}")
    lines.extend(
        [
            f"Method: {method}",
            f"Setup chat: {setup_chat_title}",
        ]
    )

    body = "\n".join(lines)
    if getattr(payment, "is_test", False):
        return f"{TEST_NOTIFICATION_BANNER}\n\n{body}"
    return body


def format_notification_text(
    payment: VenmoPayment,
    *,
    group_title: Optional[str] = None,
) -> str:
    gs = "True" if payment.goods_or_services else "False"
    method = payment.venmo_handle
    if not method.startswith("@"):
        method = f"@{method.lstrip('@')}"

    lines = [
        "🔔 Venmo Payment Notification",
        "",
    ]

    if group_title:
        lines.append(f"Group Chat: {group_title}")
    else:
        lines.append(
            "Group Chat: Unbound — reply to this message with the group title to bind"
        )

    lines.extend(
        [
            "",
            f"Name: {payment.payer_name}",
            f"Amount: {format_amount_display(payment.amount_cents)}",
        ]
    )
    memo = (getattr(payment, "memo", None) or "").strip()
    if memo:
        lines.append(f"Memo: {memo}")
    lines.extend(
        [
            f"Method: {method}",
            f"Goods/Services: {gs}",
        ]
    )

    body = "\n".join(lines)
    if getattr(payment, "is_test", False):
        return f"{TEST_NOTIFICATION_BANNER}\n\n{body}"
    return body


async def _telegram_api(
    method: str,
    payload: dict[str, Any],
    *,
    token: str,
) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    if not data.get("ok"):
        err = data.get("description") or "Unknown Telegram error"
        raise RuntimeError(err)
    return data


async def send_telegram_notification(text: str) -> tuple[int, int]:
    """Post notification to staff group. Returns (chat_id, message_id)."""
    token = _notification_bot_token()
    chat_id = _notification_chat_id()
    if not token:
        raise RuntimeError(f"{NOTIFICATION_BOT_TOKEN_ENV} is not set")
    if chat_id is None:
        raise RuntimeError(f"{PAYMENT_NOTIFICATION_CHAT_ID_ENV} is not set")

    data = await _telegram_api(
        "sendMessage",
        {"chat_id": chat_id, "text": text},
        token=token,
    )
    result = data.get("result") or {}
    message_id = int(result["message_id"])
    chat_obj = result.get("chat") or {}
    resolved_chat_id = int(chat_obj.get("id") or chat_id)
    return resolved_chat_id, message_id


async def edit_telegram_notification(
    chat_id: int,
    message_id: int,
    text: str,
) -> None:
    token = _notification_bot_token()
    if not token:
        raise RuntimeError(f"{NOTIFICATION_BOT_TOKEN_ENV} is not set")
    await _telegram_api(
        "editMessageText",
        {"chat_id": chat_id, "message_id": message_id, "text": text},
        token=token,
    )


def _apply_binding_to_payment(
    payment: VenmoPayment,
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


def _upsert_payer_binding(
    session,
    *,
    payer_name: str,
    venmo_handle: str,
    telegram_chat_id: int,
    club_id: int,
    bound_group_title_at_bind: str,
    bound_by_telegram_user_id: Optional[int],
) -> None:
    normalized = normalize_payer_name(payer_name)
    handle = normalize_venmo_handle(venmo_handle)
    now = datetime.now(timezone.utc)
    row = (
        session.query(VenmoPayerBinding)
        .filter_by(payer_name_normalized=normalized)
        .one_or_none()
    )
    if row is None:
        row = VenmoPayerBinding(
            payer_name_normalized=normalized,
            venmo_handle=handle,
        )
        session.add(row)
    row.venmo_handle = handle
    row.telegram_chat_id = int(telegram_chat_id)
    row.club_id = int(club_id)
    row.bound_group_title_at_bind = bound_group_title_at_bind[:255]
    row.last_bound_at = now
    row.last_bound_by_telegram_user_id = bound_by_telegram_user_id


def find_payment_by_notification_message(
    notification_chat_id: int,
    notification_message_id: int,
) -> Optional[VenmoPayment]:
    with get_db() as session:
        return (
            session.query(VenmoPayment)
            .filter_by(
                notification_chat_id=int(notification_chat_id),
                notification_message_id=int(notification_message_id),
            )
            .one_or_none()
        )


async def ingest_venmo_payment(
    *,
    payer_name: str,
    amount: str | int | float | Decimal,
    venmo_handle: str,
    goods_or_services: bool = False,
    paid_at: Optional[str] = None,
    source_external_id: Optional[str] = None,
    memo: Optional[str] = None,
    test: bool = False,
) -> IngestResult:
    """Create payment row, auto-bind if known payer, send Telegram notification."""
    payer = (payer_name or "").strip()
    if not payer:
        raise ValueError("payer_name is required")
    handle = normalize_venmo_handle(venmo_handle)
    if not handle:
        raise ValueError("venmo_handle is required")
    amount_cents = parse_amount_cents(amount)

    created = True
    auto_bound = False
    group_title: Optional[str] = None
    setup_warning_text: Optional[str] = None
    setup_blocked_already_linked = False

    with get_db() as session:
        if source_external_id:
            existing = (
                session.query(VenmoPayment)
                .filter_by(source_external_id=source_external_id.strip())
                .one_or_none()
            )
            if existing is not None:
                logger.info(
                    "venmo ingest: idempotent reject source_external_id=%r "
                    "existing_payment_id=%s existing_payer=%r incoming_payer=%r "
                    "incoming_amount_cents=%s incoming_handle=%r "
                    "(skipping create and telegram send)",
                    source_external_id.strip(),
                    existing.id,
                    existing.payer_name,
                    payer,
                    amount_cents,
                    handle,
                )
                return IngestResult(
                    payment_id=int(existing.id),
                    status="bound" if existing.telegram_chat_id else "unbound",
                    auto_bound=bool(existing.auto_bound),
                    created=False,
                )

        payment = VenmoPayment(
            payer_name=payer,
            amount_cents=amount_cents,
            venmo_handle=handle,
            goods_or_services=bool(goods_or_services),
            paid_at=(paid_at or "").strip() or None,
            source_external_id=(source_external_id or "").strip() or None,
            memo=(memo or "").strip() or None,
            is_test=bool(test),
        )
        session.add(payment)
        session.flush()

        setup_attempt = None
        setup_bound_via = BOUND_VIA_SPECIAL_AMOUNT
        # Match pending first-time setup attempts whenever they exist (created by
        # run_test_bot.py). Ingest runs on the API/web process, not the bot worker.
        setup_attempt = match_pending_memo_setup_in_session(
            session,
            payment_method_slug="venmo",
            venmo_handle=handle,
            memo=memo,
        )
        if setup_attempt is not None:
            setup_bound_via = BOUND_VIA_MEMO_EMOJI
        else:
            setup_attempt = match_pending_venmo_setup_in_session(
                session,
                amount_cents=amount_cents,
                venmo_handle=handle,
            )
        if setup_attempt is not None:
            live_title = resolve_display_group_title(int(setup_attempt.telegram_chat_id))
            club_id_setup = int(setup_attempt.club_id)
            if not live_title:
                logger.warning(
                    "venmo ingest: setup attempt matched but group title missing "
                    "attempt_id=%s chat_id=%s payment_id=%s handle=%r memo=%r",
                    setup_attempt.id,
                    setup_attempt.telegram_chat_id,
                    payment.id,
                    handle,
                    memo,
                )
            if live_title:
                existing_link = find_existing_venmo_link_for_setup(
                    session,
                    payer_name=payer,
                    setup_chat_id=int(setup_attempt.telegram_chat_id),
                )
                if existing_link is not None:
                    linked_title = (
                        resolve_display_group_title(int(existing_link.linked_chat_id))
                        or "—"
                    )
                    last_deposit_at = get_last_bound_deposit_at(
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
                    logger.warning(
                        "venmo ingest: setup match blocked — already linked "
                        "attempt_id=%s payment_id=%s linked_chat_id=%s via=%s",
                        setup_attempt.id,
                        payment.id,
                        existing_link.linked_chat_id,
                        existing_link.via,
                    )
                elif complete_attempt_in_session(
                    session,
                    setup_attempt,
                    venmo_payment_id=int(payment.id),
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
                        venmo_handle=payment.venmo_handle,
                        telegram_chat_id=int(setup_attempt.telegram_chat_id),
                        club_id=club_id_setup,
                        bound_group_title_at_bind=live_title,
                        bound_by_telegram_user_id=None,
                    )
                    record_group_binding_in_session(
                        session,
                        telegram_chat_id=int(setup_attempt.telegram_chat_id),
                        club_id=club_id_setup,
                        payment_method_slug="venmo",
                        bound_via=setup_bound_via,
                        variant_id=int(setup_attempt.variant_id),
                        venmo_handle=handle,
                        first_bind_attempt_id=int(setup_attempt.id),
                    )
                    logger.info(
                        "venmo ingest: setup bind matched attempt_id=%s payment_id=%s "
                        "chat_id=%s via=%s",
                        setup_attempt.id,
                        payment.id,
                        setup_attempt.telegram_chat_id,
                        setup_bound_via,
                    )

        if not auto_bound and not setup_blocked_already_linked:
            binding = (
                session.query(VenmoPayerBinding)
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
        text = format_notification_text(
            payment,
            group_title=group_title,
        )

    if debug_notification_enabled():
        configured_chat = _notification_chat_id()
        has_token = bool(_notification_bot_token())
        logger.info(
            "venmo ingest: sending telegram notification payment_id=%s "
            "chat_id=%s token_configured=%s text_len=%s auto_bound=%s",
            payment_id,
            configured_chat,
            has_token,
            len(text),
            auto_bound,
        )

    if setup_warning_text:
        await send_telegram_notification(setup_warning_text)

    notif_chat_id, notif_message_id = await send_telegram_notification(text)

    if debug_notification_enabled():
        logger.info(
            "venmo ingest: telegram sent payment_id=%s chat_id=%s message_id=%s",
            payment_id,
            notif_chat_id,
            notif_message_id,
        )

    with get_db() as session:
        payment = session.query(VenmoPayment).filter_by(id=payment_id).one()
        payment.notification_chat_id = notif_chat_id
        payment.notification_message_id = notif_message_id

    status = "bound" if auto_bound else "unbound"
    logger.info(
        "venmo payment ingested id=%s payer=%r amount_cents=%s auto_bound=%s",
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


async def bind_venmo_payment_by_id(
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
    live_title = group.group_title

    with get_db() as session:
        payment = session.query(VenmoPayment).filter_by(id=int(payment_id)).one_or_none()
        if payment is None:
            return BindResult(ok=False, error="Payment not found.")

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
            venmo_handle=payment.venmo_handle,
            telegram_chat_id=group.telegram_chat_id,
            club_id=group.club_id,
            bound_group_title_at_bind=group.group_title,
            bound_by_telegram_user_id=bound_by_telegram_user_id,
        )

        variant_id = infer_variant_id_for_venmo_handle(
            int(group.club_id),
            payment.venmo_handle,
        )
        cancel_pending_attempts_for_chat_in_session(
            session,
            telegram_chat_id=int(group.telegram_chat_id),
            payment_method_slug="venmo",
        )
        record_group_binding_in_session(
            session,
            telegram_chat_id=int(group.telegram_chat_id),
            club_id=int(group.club_id),
            payment_method_slug="venmo",
            bound_via=bound_via,
            variant_id=variant_id,
            venmo_handle=payment.venmo_handle,
            bound_by_telegram_user_id=bound_by_telegram_user_id,
        )

        live_title = resolve_display_group_title(group.telegram_chat_id) or group.group_title
        if payment.notification_chat_id and payment.notification_message_id:
            notif_chat_id = int(payment.notification_chat_id)
            notif_message_id = int(payment.notification_message_id)
            text = format_notification_text(payment, group_title=live_title)
        else:
            text = None

    if notif_chat_id and notif_message_id and text:
        await edit_telegram_notification(notif_chat_id, notif_message_id, text)

    return BindResult(
        ok=True,
        bound_group=BoundGroup(
            telegram_chat_id=group.telegram_chat_id,
            club_id=group.club_id,
            group_title=live_title,
        ),
    )


async def bind_venmo_payment_from_reply(
    *,
    notification_chat_id: int,
    notification_message_id: int,
    group_title_input: str,
    bound_by_telegram_user_id: int,
) -> BindResult:
    """Bind or rebind a payment from a reply in the notification group."""
    with get_db() as session:
        payment = (
            session.query(VenmoPayment)
            .filter_by(
                notification_chat_id=int(notification_chat_id),
                notification_message_id=int(notification_message_id),
            )
            .one_or_none()
        )
        if payment is None:
            return BindResult(ok=False, error="No payment found for this notification.")
        payment_id = int(payment.id)

    return await bind_venmo_payment_by_id(
        payment_id=payment_id,
        group_title_input=group_title_input,
        bound_by_telegram_user_id=int(bound_by_telegram_user_id),
        bound_via=BOUND_VIA_MANUAL_NOTIFICATION,
    )
