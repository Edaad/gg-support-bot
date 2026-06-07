"""Crypto payment ingest, Telegram notifications, and manual group binding."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from bot.services.payment_method_binding import (
    BOUND_VIA_MANUAL_DASHBOARD,
    BOUND_VIA_MANUAL_NOTIFICATION,
)
from bot.services.venmo_payments import (
    BindResult,
    BoundGroup,
    IngestResult,
    edit_telegram_notification,
    escape_notification_html,
    format_amount_display,
    format_paid_at_display,
    parse_amount_cents,
    resolve_bound_group,
    resolve_display_group_title,
    send_telegram_notification,
    TEST_NOTIFICATION_BANNER,
)
from db.connection import get_db
from db.models import Club, CryptoPayment
from notification.chat_id import format_linked_chat_footer, resolve_notification_linked_chat_id

logger = logging.getLogger(__name__)

WEBHOOK_SECRET_ENV = "CRYPTO_ZAPIER_WEBHOOK_SECRET"

ALERT_NAME_CLUBGTO = "ClubGTO Crypto Payment"
ALERT_NAME_RT_AT_CC = "RT/AT/CC Crypto Payment"

ALERT_SCOPE_CLUBGTO = "clubgto"
ALERT_SCOPE_RT_AT_CC = "rt_at_cc"

ALERT_NAME_TO_SCOPE: dict[str, str] = {
    ALERT_NAME_CLUBGTO.lower(): ALERT_SCOPE_CLUBGTO,
    ALERT_NAME_RT_AT_CC.lower(): ALERT_SCOPE_RT_AT_CC,
}

CLUB_NAME_TO_ALERT_SCOPE: dict[str, str] = {
    "ClubGTO": ALERT_SCOPE_CLUBGTO,
    "Round Table": ALERT_SCOPE_RT_AT_CC,
    "Creator Club": ALERT_SCOPE_RT_AT_CC,
}

ALERT_SCOPE_LABELS: dict[str, str] = {
    ALERT_SCOPE_CLUBGTO: "ClubGTO",
    ALERT_SCOPE_RT_AT_CC: "RT/AT/CC",
}


def resolve_alert_scope(alert_name: str) -> str:
    """Map Arkham alert name to clubgto or rt_at_cc bucket."""
    key = (alert_name or "").strip().lower()
    if not key:
        raise ValueError("alert_name is required")
    scope = ALERT_NAME_TO_SCOPE.get(key)
    if scope is None:
        allowed = f"{ALERT_NAME_CLUBGTO!r} or {ALERT_NAME_RT_AT_CC!r}"
        raise ValueError(f"Unknown alert_name {alert_name!r}; expected {allowed}")
    return scope


def alert_scope_for_club_name(club_name: str | None) -> Optional[str]:
    if not club_name:
        return None
    return CLUB_NAME_TO_ALERT_SCOPE.get(club_name.strip())


def alert_scope_for_club_id(club_id: int) -> Optional[str]:
    with get_db() as session:
        club = session.query(Club).filter_by(id=int(club_id)).one_or_none()
        if club is None:
            return None
        return alert_scope_for_club_name(club.name)


def validate_bind_alert_scope(
    payment: CryptoPayment,
    *,
    bound_club_id: int,
) -> Optional[BindResult]:
    """Return BindResult error when group club does not match payment alert scope."""
    payment_scope = (payment.alert_scope or "").strip()
    with get_db() as session:
        club = session.query(Club).filter_by(id=int(bound_club_id)).one_or_none()
        if club is None:
            return BindResult(ok=False, error="Club not found.")
        bound_scope = alert_scope_for_club_name(club.name)

    if bound_scope == payment_scope:
        return None

    if payment_scope == ALERT_SCOPE_CLUBGTO:
        return BindResult(
            ok=False,
            error=(
                "This payment is for ClubGTO only. "
                "Reply with a GTO / PLAYER_ID / NAME group title."
            ),
        )
    return BindResult(
        ok=False,
        error=(
            "This payment is for RT/AT/CC only. "
            "Reply with an RT, AT, or CC group title."
        ),
    )


def shorten_address(address: str) -> str:
    raw = (address or "").strip()
    if len(raw) <= 12:
        return raw
    return f"{raw[:6]}…{raw[-4:]}"


def format_from_label(payment: CryptoPayment) -> str:
    entity = (payment.from_entity_name or "").strip()
    addr = shorten_address(payment.from_address)
    if entity:
        return f"{entity} ({addr})"
    return payment.from_address


def format_notification_text(
    payment: CryptoPayment,
    *,
    group_title: Optional[str] = None,
    telegram_chat_id: Optional[int] = None,
) -> str:
    token = escape_notification_html((payment.token_symbol or "").strip().upper())
    chain = escape_notification_html((payment.chain or "").strip().upper())
    amount_line = (
        f"{format_amount_display(payment.amount_cents, bold=True)} {token}".strip()
    )
    scope_label = escape_notification_html(
        ALERT_SCOPE_LABELS.get(payment.alert_scope or "", payment.alert_scope or "")
    )

    lines = [
        "🔔 Crypto Payment Notification",
        "",
    ]
    if group_title:
        lines.append(f"Group Chat: {escape_notification_html(group_title)}")
    else:
        lines.append(
            "Group Chat: Unbound — reply to this message with the group title to bind"
        )

    lines.extend(
        [
            "",
            f"Alert: {scope_label}",
            f"Amount: {amount_line}",
            f"Chain: {chain}",
            f"From: {escape_notification_html(format_from_label(payment))}",
        ]
    )
    if payment.paid_at:
        lines.append(f"Paid: {escape_notification_html(format_paid_at_display(payment.paid_at))}")

    body = "\n".join(lines)
    footer = format_linked_chat_footer(
        resolve_notification_linked_chat_id(
            payment,
            telegram_chat_id=telegram_chat_id,
        )
    )
    if footer:
        body = f"{body}{footer}"
    if getattr(payment, "is_test", False):
        return f"{TEST_NOTIFICATION_BANNER}\n\n{body}"
    return body


def _apply_binding_to_payment(
    payment: CryptoPayment,
    *,
    telegram_chat_id: int,
    club_id: int,
    bound_group_title_at_bind: str,
    bound_by_telegram_user_id: Optional[int] = None,
) -> None:
    now = datetime.now(timezone.utc)
    payment.telegram_chat_id = int(telegram_chat_id)
    payment.club_id = int(club_id)
    payment.bound_group_title_at_bind = bound_group_title_at_bind[:255]
    payment.auto_bound = False
    payment.bound_at = now
    payment.bound_by_telegram_user_id = bound_by_telegram_user_id


def find_crypto_payment_by_notification_message(
    notification_chat_id: int,
    notification_message_id: int,
) -> Optional[CryptoPayment]:
    with get_db() as session:
        return (
            session.query(CryptoPayment)
            .filter_by(
                notification_chat_id=int(notification_chat_id),
                notification_message_id=int(notification_message_id),
            )
            .one_or_none()
        )


async def ingest_crypto_payment(
    *,
    amount: str | int | float | Decimal,
    token_symbol: str,
    chain: str,
    from_address: str,
    to_address: str,
    transaction_hash: str,
    alert_name: str,
    token_name: Optional[str] = None,
    from_entity_name: Optional[str] = None,
    paid_at: Optional[str] = None,
    source_external_id: Optional[str] = None,
    test: bool = False,
) -> IngestResult:
    """Create payment row (always unbound) and send Telegram notification."""
    symbol = (token_symbol or "").strip().upper()
    if not symbol:
        raise ValueError("token_symbol is required")
    chain_norm = (chain or "").strip().lower()
    if not chain_norm:
        raise ValueError("chain is required")
    from_addr = (from_address or "").strip()
    if not from_addr:
        raise ValueError("from_address is required")
    to_addr = (to_address or "").strip()
    if not to_addr:
        raise ValueError("to_address is required")
    tx_hash = (transaction_hash or "").strip()
    if not tx_hash:
        raise ValueError("transaction_hash is required")
    amount_cents = parse_amount_cents(amount)
    alert = (alert_name or "").strip()
    alert_scope = resolve_alert_scope(alert)

    with get_db() as session:
        if source_external_id:
            existing = (
                session.query(CryptoPayment)
                .filter_by(source_external_id=source_external_id.strip())
                .one_or_none()
            )
            if existing is not None:
                payment_id = int(existing.id)
                needs_notification = existing.notification_message_id is None
                if needs_notification:
                    text = format_notification_text(existing)
                logger.info(
                    "crypto ingest: idempotent reject source_external_id=%r "
                    "existing_payment_id=%s needs_notification=%s",
                    source_external_id.strip(),
                    payment_id,
                    needs_notification,
                )
                if needs_notification:
                    notif_chat_id, notif_message_id = await send_telegram_notification(
                        text
                    )
                    existing.notification_chat_id = notif_chat_id
                    existing.notification_message_id = notif_message_id
                return IngestResult(
                    payment_id=payment_id,
                    status="bound" if existing.telegram_chat_id else "unbound",
                    auto_bound=False,
                    created=False,
                )

        payment = CryptoPayment(
            amount_cents=amount_cents,
            token_symbol=symbol,
            token_name=(token_name or "").strip() or None,
            chain=chain_norm,
            from_address=from_addr,
            from_entity_name=(from_entity_name or "").strip() or None,
            to_address=to_addr,
            transaction_hash=tx_hash,
            paid_at=(paid_at or "").strip() or None,
            source_external_id=(source_external_id or "").strip() or None,
            alert_name=alert,
            alert_scope=alert_scope,
            is_test=bool(test),
        )
        session.add(payment)
        session.flush()
        payment_id = int(payment.id)
        text = format_notification_text(payment)

    notif_chat_id, notif_message_id = await send_telegram_notification(text)

    with get_db() as session:
        payment = session.query(CryptoPayment).filter_by(id=payment_id).one()
        payment.notification_chat_id = notif_chat_id
        payment.notification_message_id = notif_message_id

    logger.info(
        "crypto payment ingested id=%s amount_cents=%s token=%s chain=%s alert_scope=%s",
        payment_id,
        amount_cents,
        symbol,
        chain_norm,
        alert_scope,
    )
    return IngestResult(
        payment_id=payment_id,
        status="unbound",
        auto_bound=False,
        created=True,
    )


async def bind_crypto_payment_by_id(
    *,
    payment_id: int,
    group_title_input: str,
    bound_by_telegram_user_id: Optional[int] = None,
    bound_via: str = BOUND_VIA_MANUAL_DASHBOARD,
) -> BindResult:
    """Bind or rebind a crypto payment to a support group."""
    del bound_via  # crypto has no group_payment_method_bindings row
    result = resolve_bound_group(group_title_input)
    if not result.ok or result.bound_group is None:
        return result

    group = result.bound_group
    notif_chat_id: Optional[int] = None
    notif_message_id: Optional[int] = None
    live_title = group.group_title

    with get_db() as session:
        payment = (
            session.query(CryptoPayment).filter_by(id=int(payment_id)).one_or_none()
        )
        if payment is None:
            return BindResult(ok=False, error="Payment not found.")

        scope_err = validate_bind_alert_scope(
            payment,
            bound_club_id=group.club_id,
        )
        if scope_err is not None:
            return scope_err

        _apply_binding_to_payment(
            payment,
            telegram_chat_id=group.telegram_chat_id,
            club_id=group.club_id,
            bound_group_title_at_bind=group.group_title,
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


async def bind_crypto_payment_from_reply(
    *,
    notification_chat_id: int,
    notification_message_id: int,
    group_title_input: str,
    bound_by_telegram_user_id: int,
) -> BindResult:
    """Bind or rebind a crypto payment from a reply in the notification group."""
    with get_db() as session:
        payment = (
            session.query(CryptoPayment)
            .filter_by(
                notification_chat_id=int(notification_chat_id),
                notification_message_id=int(notification_message_id),
            )
            .one_or_none()
        )
        if payment is None:
            return BindResult(ok=False, error="No payment found for this notification.")
        payment_id = int(payment.id)

    return await bind_crypto_payment_by_id(
        payment_id=payment_id,
        group_title_input=group_title_input,
        bound_by_telegram_user_id=int(bound_by_telegram_user_id),
        bound_via=BOUND_VIA_MANUAL_NOTIFICATION,
    )
