"""Persisted audit log for payment binding and notification sync."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from db.connection import get_db
from db.models import PaymentBindingEvent

logger = logging.getLogger(__name__)

EVENT_PAYMENT_BOUND = "payment_bound"
EVENT_PAYMENT_AUTO_BOUND = "payment_auto_bound"
EVENT_GROUP_BINDING_UPDATED = "group_binding_updated"
EVENT_NOTIFICATION_SENT = "notification_sent"
EVENT_NOTIFICATION_EDIT_OK = "notification_edit_ok"
EVENT_NOTIFICATION_EDIT_FAILED = "notification_edit_failed"
EVENT_NOTIFICATION_EDIT_SKIPPED = "notification_edit_skipped"


@dataclass(frozen=True)
class BindingEventRecord:
    event_type: str
    payment_method_slug: str
    payment_id: int | None = None
    bind_attempt_id: int | None = None
    group_binding_id: int | None = None
    telegram_chat_id: int | None = None
    club_id: int | None = None
    bound_group_title: str | None = None
    bound_via: str | None = None
    auto_bound: bool | None = None
    actor_telegram_user_id: int | None = None
    notification_chat_id: int | None = None
    notification_message_id: int | None = None
    previous_telegram_chat_id: int | None = None
    error_message: str | None = None


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]


def record_binding_event_in_session(session, record: BindingEventRecord) -> int:
    row = PaymentBindingEvent(
        event_type=record.event_type,
        payment_method_slug=(record.payment_method_slug or "").strip().lower(),
        payment_id=record.payment_id,
        bind_attempt_id=record.bind_attempt_id,
        group_binding_id=record.group_binding_id,
        telegram_chat_id=record.telegram_chat_id,
        club_id=record.club_id,
        bound_group_title=_truncate(record.bound_group_title, 255),
        bound_via=_truncate(record.bound_via, 32),
        auto_bound=record.auto_bound,
        actor_telegram_user_id=record.actor_telegram_user_id,
        notification_chat_id=record.notification_chat_id,
        notification_message_id=record.notification_message_id,
        previous_telegram_chat_id=record.previous_telegram_chat_id,
        error_message=record.error_message,
    )
    session.add(row)
    session.flush()
    logger.info(
        "binding_event id=%s type=%s method=%s payment_id=%s chat_id=%s",
        row.id,
        row.event_type,
        row.payment_method_slug,
        row.payment_id,
        row.telegram_chat_id,
    )
    return int(row.id)


def record_binding_event(record: BindingEventRecord) -> int:
    with get_db() as session:
        event_id = record_binding_event_in_session(session, record)
    return event_id


def record_payment_bound(
    *,
    payment_method_slug: str,
    payment_id: int,
    telegram_chat_id: int,
    club_id: int,
    bound_group_title: str,
    bound_via: str,
    auto_bound: bool,
    actor_telegram_user_id: int | None = None,
    notification_chat_id: int | None = None,
    notification_message_id: int | None = None,
    previous_telegram_chat_id: int | None = None,
    bind_attempt_id: int | None = None,
) -> int:
    event_type = EVENT_PAYMENT_AUTO_BOUND if auto_bound else EVENT_PAYMENT_BOUND
    event_id = record_binding_event(
        BindingEventRecord(
            event_type=event_type,
            payment_method_slug=payment_method_slug,
            payment_id=payment_id,
            bind_attempt_id=bind_attempt_id,
            telegram_chat_id=telegram_chat_id,
            club_id=club_id,
            bound_group_title=bound_group_title,
            bound_via=bound_via,
            auto_bound=auto_bound,
            actor_telegram_user_id=actor_telegram_user_id,
            notification_chat_id=notification_chat_id,
            notification_message_id=notification_message_id,
            previous_telegram_chat_id=previous_telegram_chat_id,
        )
    )
    if not auto_bound:
        try:
            from bot.services.deposit_funnel_events import (
                record_payment_funnel_on_manual_bind_from_event,
            )

            record_payment_funnel_on_manual_bind_from_event(
                payment_method_slug=payment_method_slug,
                payment_id=payment_id,
                telegram_chat_id=int(telegram_chat_id),
                club_id=int(club_id) if club_id is not None else None,
                bind_attempt_id=bind_attempt_id,
            )
        except Exception:
            logger.debug(
                "record_payment_bound: funnel manual bind failed payment_id=%s",
                payment_id,
                exc_info=True,
            )
    return event_id


def record_group_binding_event(
    *,
    payment_method_slug: str,
    group_binding_id: int,
    telegram_chat_id: int,
    club_id: int,
    bound_via: str,
    bound_by_telegram_user_id: int | None = None,
    bind_attempt_id: int | None = None,
) -> int:
    return record_binding_event(
        BindingEventRecord(
            event_type=EVENT_GROUP_BINDING_UPDATED,
            payment_method_slug=payment_method_slug,
            group_binding_id=group_binding_id,
            telegram_chat_id=telegram_chat_id,
            club_id=club_id,
            bound_via=bound_via,
            actor_telegram_user_id=bound_by_telegram_user_id,
            bind_attempt_id=bind_attempt_id,
        )
    )


def track_ingest_notification(
    *,
    payment_method_slug: str,
    payment_id: int,
    notification_chat_id: int,
    notification_message_id: int,
    telegram_chat_id: int | None,
    club_id: int | None,
    bound_group_title: str | None,
    auto_bound: bool,
    bound_via: str | None = None,
    bind_attempt_id: int | None = None,
) -> None:
    """Record auto-bind (if any) and initial notification delivery for ingest."""
    if auto_bound and telegram_chat_id is not None and club_id is not None:
        record_payment_bound(
            payment_method_slug=payment_method_slug,
            payment_id=payment_id,
            telegram_chat_id=int(telegram_chat_id),
            club_id=int(club_id),
            bound_group_title=bound_group_title or "",
            bound_via=bound_via or "",
            auto_bound=True,
            notification_chat_id=notification_chat_id,
            notification_message_id=notification_message_id,
            bind_attempt_id=bind_attempt_id,
        )
    record_notification_sent(
        payment_method_slug=payment_method_slug,
        payment_id=payment_id,
        notification_chat_id=notification_chat_id,
        notification_message_id=notification_message_id,
        telegram_chat_id=telegram_chat_id,
        club_id=club_id,
        bound_group_title=bound_group_title,
        auto_bound=auto_bound,
        bound_via=bound_via if auto_bound and bind_attempt_id is not None else None,
    )


def record_notification_sent(
    *,
    payment_method_slug: str,
    payment_id: int,
    notification_chat_id: int,
    notification_message_id: int,
    telegram_chat_id: int | None,
    club_id: int | None,
    bound_group_title: str | None,
    auto_bound: bool,
    bound_via: str | None = None,
) -> int:
    return record_binding_event(
        BindingEventRecord(
            event_type=EVENT_NOTIFICATION_SENT,
            payment_method_slug=payment_method_slug,
            payment_id=payment_id,
            notification_chat_id=notification_chat_id,
            notification_message_id=notification_message_id,
            telegram_chat_id=telegram_chat_id,
            club_id=club_id,
            bound_group_title=bound_group_title,
            bound_via=bound_via,
            auto_bound=auto_bound,
        )
    )


async def sync_payment_notification_edit(
    *,
    payment_method_slug: str,
    payment_id: int,
    notification_chat_id: int | None,
    notification_message_id: int | None,
    text: str | None,
    bound_via: str | None = None,
    actor_telegram_user_id: int | None = None,
    telegram_chat_id: int | None = None,
    club_id: int | None = None,
    bound_group_title: str | None = None,
    auto_bound: bool = False,
    reply_markup: dict | None = None,
) -> bool:
    """Edit a payment notification and persist the outcome. Returns True on success."""
    from bot.services.venmo_payments import edit_telegram_notification

    base = dict(
        payment_method_slug=payment_method_slug,
        payment_id=payment_id,
        notification_chat_id=notification_chat_id,
        notification_message_id=notification_message_id,
        bound_via=bound_via,
        actor_telegram_user_id=actor_telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        club_id=club_id,
        bound_group_title=bound_group_title,
        auto_bound=auto_bound,
    )

    if not notification_chat_id or not notification_message_id or not text:
        record_binding_event(
            BindingEventRecord(
                event_type=EVENT_NOTIFICATION_EDIT_SKIPPED,
                error_message="missing notification ids or text",
                **base,
            )
        )
        return False

    try:
        edit_kwargs: dict = {}
        if reply_markup is not None:
            edit_kwargs["reply_markup"] = reply_markup
        await edit_telegram_notification(
            int(notification_chat_id),
            int(notification_message_id),
            text,
            **edit_kwargs,
        )
    except Exception as exc:
        record_binding_event(
            BindingEventRecord(
                event_type=EVENT_NOTIFICATION_EDIT_FAILED,
                error_message=str(exc)[:2000],
                **base,
            )
        )
        raise

    record_binding_event(
        BindingEventRecord(
            event_type=EVENT_NOTIFICATION_EDIT_OK,
            **base,
        )
    )
    if reply_markup is not None:
        from bot.services.payment_bind_logging import log_notification_edit

        log_notification_edit(
            method_slug=payment_method_slug,
            payment_id=int(payment_id),
            notification_chat_id=int(notification_chat_id),
            notification_message_id=int(notification_message_id),
            has_keyboard=bool(reply_markup.get("inline_keyboard")),
            keyboard_kind="cleared" if not reply_markup.get("inline_keyboard") else "present",
            reason="sync_payment_notification_edit",
        )
    return True


def _payment_ids_with_notification_sync(session, *, payment_method_slug: str) -> set[int]:
    """Payment ids whose Telegram notification is known to show a bound group."""
    slug = (payment_method_slug or "").strip().lower()
    synced: set[int] = set()
    rows = (
        session.query(PaymentBindingEvent.payment_id, PaymentBindingEvent.event_type)
        .filter(
            PaymentBindingEvent.payment_method_slug == slug,
            PaymentBindingEvent.payment_id.isnot(None),
            PaymentBindingEvent.event_type.in_(
                (
                    EVENT_NOTIFICATION_EDIT_OK,
                    EVENT_NOTIFICATION_SENT,
                )
            ),
        )
        .all()
    )
    sent_auto_bound: set[int] = set()
    for payment_id, event_type in rows:
        pid = int(payment_id)
        if event_type == EVENT_NOTIFICATION_EDIT_OK:
            synced.add(pid)
        elif event_type == EVENT_NOTIFICATION_SENT:
            sent_auto_bound.add(pid)
    if not sent_auto_bound:
        return synced
    sent_rows = (
        session.query(PaymentBindingEvent.payment_id)
        .filter(
            PaymentBindingEvent.payment_method_slug == slug,
            PaymentBindingEvent.event_type == EVENT_NOTIFICATION_SENT,
            PaymentBindingEvent.auto_bound.is_(True),
            PaymentBindingEvent.payment_id.in_(sent_auto_bound),
        )
        .distinct()
        .all()
    )
    synced.update(int(row[0]) for row in sent_rows)
    return synced


def payments_missing_notification_sync(
    session,
    *,
    payment_method_slug: str,
    limit: int = 100,
) -> list[dict]:
    """Return bound payments with no persisted proof the Telegram message shows binding."""
    from db.models import CashAppPayment, CryptoPayment, PayPalPayment, VenmoPayment, ZellePayment

    slug = (payment_method_slug or "").strip().lower()
    model_map = {
        "venmo": VenmoPayment,
        "zelle": ZellePayment,
        "cashapp": CashAppPayment,
        "paypal": PayPalPayment,
        "crypto": CryptoPayment,
    }
    model = model_map.get(slug)
    if model is None:
        return []

    synced_ids = _payment_ids_with_notification_sync(session, payment_method_slug=slug)
    rows = (
        session.query(model)
        .filter(
            model.telegram_chat_id.isnot(None),
            model.is_test.is_(False),
            model.notification_chat_id.isnot(None),
            model.notification_message_id.isnot(None),
        )
        .order_by(model.created_at.desc())
        .limit(int(limit) * 3)
        .all()
    )
    missing = []
    for row in rows:
        if int(row.id) in synced_ids:
            continue
        missing.append(
            {
                "payment_id": int(row.id),
                "payment_method_slug": slug,
                "telegram_chat_id": int(row.telegram_chat_id),
                "bound_group_title_at_bind": row.bound_group_title_at_bind,
                "notification_chat_id": row.notification_chat_id,
                "notification_message_id": row.notification_message_id,
                "auto_bound": bool(row.auto_bound),
                "bound_at": row.bound_at,
            }
        )
        if len(missing) >= int(limit):
            break
    return missing
