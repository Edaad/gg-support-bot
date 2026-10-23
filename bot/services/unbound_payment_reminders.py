"""Slack reminders for payments that are still unbound 5 minutes after ingest."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_

from db.connection import get_db
from db.models import (
    CashAppPayment,
    CryptoPayment,
    PayPalPayment,
    UnboundPaymentReminder,
    VenmoPayment,
    ZellePayment,
)

logger = logging.getLogger(__name__)

REMINDER_INTERVAL = timedelta(minutes=5)
JOB_POLL_INTERVAL = timedelta(seconds=30)
SLACK_SOURCE = "unbound_payment_reminder"

_PAYMENT_MODELS = {
    "venmo": VenmoPayment,
    "zelle": ZellePayment,
    "cashapp": CashAppPayment,
    "paypal": PayPalPayment,
    "crypto": CryptoPayment,
}

_METHOD_LABELS = {
    "crypto": "Crypto",
    "venmo": "Venmo",
    "zelle": "Zelle",
    "cashapp": "Cash App",
    "paypal": "PayPal",
}


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def reminder_is_due(
    *,
    created_at: datetime,
    last_reminded_at: datetime | None,
    now: datetime,
    interval: timedelta = REMINDER_INTERVAL,
) -> bool:
    """First ping once the payment is 5 minutes old, then every 5 minutes."""
    created = _aware(created_at)
    current = _aware(now)
    if created > current - interval:
        return False
    if last_reminded_at is None:
        return True
    return _aware(last_reminded_at) <= current - interval


def waiting_minutes(created_at: datetime, now: datetime) -> int:
    elapsed = _aware(now) - _aware(created_at)
    return max(int(elapsed.total_seconds() // 60), 5)


def _slack_code(title: str) -> str:
    safe = (title or "").strip().replace("`", "'")
    return f"`{safe}`"


def _handle_line(slug: str, payment: object) -> str | None:
    if slug == "venmo":
        handle = (getattr(payment, "venmo_handle", None) or "").strip()
        if not handle:
            return None
        if not handle.startswith("@"):
            handle = f"@{handle.lstrip('@')}"
        return f"Handle: {handle}"
    if slug == "cashapp":
        handle = (getattr(payment, "cashapp_handle", None) or "").strip()
        if not handle:
            return None
        if not handle.startswith("$"):
            handle = f"${handle.lstrip('$')}"
        return f"Handle: {handle}"
    if slug == "zelle":
        recipient = (getattr(payment, "zelle_recipient", None) or "").strip()
        return f"Handle: {recipient}" if recipient else None
    if slug == "paypal":
        email = (getattr(payment, "paypal_email", None) or "").strip()
        return f"Handle: {email}" if email else None
    return None


def format_unbound_payment_reminder(
    payment: object,
    *,
    method_slug: str,
    waiting_minutes_value: int,
    candidate_titles: list[str] | None = None,
) -> str:
    """Slack body for one still-unbound payment."""
    slug = (method_slug or "").strip().lower()
    lines = [
        (
            f"*{waiting_minutes_value} minutes have passed and this payment "
            "is still unbound — match it to a group.*"
        ),
        "",
        f"Method: {_METHOD_LABELS.get(slug, slug)}",
    ]
    if slug == "crypto":
        from bot.services.crypto_payments import (
            format_from_label,
            sender_is_exchange,
        )
        from bot.services.venmo_payments import (
            format_amount_display,
            format_paid_at_display,
        )

        alert = (getattr(payment, "alert_name", None) or "").strip()
        if alert:
            lines.append(f"Alert: {alert}")
        lines.append(
            f"Amount: {format_amount_display(int(payment.amount_cents), bold=False)}"
        )
        chain = (getattr(payment, "chain", None) or "").strip().upper()
        if chain:
            lines.append(f"Chain: {chain}")
        lines.append(f"From: {format_from_label(payment)}")
        tx_hash = (getattr(payment, "transaction_hash", None) or "").strip()
        if tx_hash:
            lines.append(f"Tx: {tx_hash}")
        paid_at = (getattr(payment, "paid_at", None) or "").strip()
        if paid_at:
            lines.append(f"Paid: {format_paid_at_display(paid_at)}")
        if sender_is_exchange(getattr(payment, "from_entity_name", None)):
            titles: list[str] = []
        else:
            titles = list(candidate_titles or [])
    else:
        from bot.services.venmo_payments import format_amount_display

        name = (getattr(payment, "payer_name", None) or "").strip()
        if name:
            lines.append(f"Name: {name}")
        lines.append(
            f"Amount: {format_amount_display(int(payment.amount_cents), bold=False)}"
        )
        memo = (getattr(payment, "memo", None) or "").strip()
        if memo:
            lines.append(f"Memo: {memo}")
        handle = _handle_line(slug, payment)
        if handle:
            lines.append(handle)
        titles = list(candidate_titles or [])

    titles = [title for title in titles if (title or "").strip()]
    if titles:
        lines.append("")
        lines.append("Possible groups:")
        lines.extend(_slack_code(title) for title in titles if (title or "").strip())
    return "\n".join(lines)


def arm_unbound_payment_reminder(*, payment_method_slug: str, payment_id: int) -> None:
    """Track a newly ingested unbound payment. Test payments and old rows are skipped."""
    slug = (payment_method_slug or "").strip().lower()
    model = _PAYMENT_MODELS.get(slug)
    if model is None:
        return
    with get_db() as session:
        payment = session.get(model, int(payment_id))
        if payment is None or getattr(payment, "is_test", False):
            return
        if getattr(payment, "telegram_chat_id", None) is not None:
            return
        existing = (
            session.query(UnboundPaymentReminder)
            .filter_by(payment_method_slug=slug, payment_id=int(payment_id))
            .one_or_none()
        )
        if existing is not None:
            return
        created_at = getattr(payment, "created_at", None) or datetime.now(timezone.utc)
        session.add(
            UnboundPaymentReminder(
                payment_method_slug=slug,
                payment_id=int(payment_id),
                created_at=_aware(created_at),
            )
        )


def clear_unbound_payment_reminder(
    *, payment_method_slug: str, payment_id: int
) -> None:
    slug = (payment_method_slug or "").strip().lower()
    with get_db() as session:
        (
            session.query(UnboundPaymentReminder)
            .filter_by(payment_method_slug=slug, payment_id=int(payment_id))
            .delete(synchronize_session=False)
        )


def _candidate_titles(session, payment: object, slug: str) -> list[str]:
    from bot.services.crypto_payments import sender_is_exchange
    from bot.services.payment_bind_candidates import candidates_for_payment

    if slug == "crypto" and sender_is_exchange(
        getattr(payment, "from_entity_name", None)
    ):
        return []
    candidates = candidates_for_payment(session, payment, slug)
    return [c.group_title for c in candidates if (c.group_title or "").strip()]


def list_due_reminders(now: datetime | None = None) -> list[UnboundPaymentReminder]:
    current = _aware(now or datetime.now(timezone.utc))
    cutoff = current - REMINDER_INTERVAL
    with get_db() as session:
        rows = (
            session.query(UnboundPaymentReminder)
            .filter(
                or_(
                    and_(
                        UnboundPaymentReminder.last_reminded_at.is_(None),
                        UnboundPaymentReminder.created_at <= cutoff,
                    ),
                    UnboundPaymentReminder.last_reminded_at <= cutoff,
                )
            )
            .all()
        )
        session.expunge_all()
        return rows


async def send_due_unbound_reminders(now: datetime | None = None) -> int:
    """Post one Slack reminder per due unbound payment. Returns the send count."""
    from bot.services.slack_ops_notify import notify_slack_escalation

    current = _aware(now or datetime.now(timezone.utc))
    sent = 0
    for row in list_due_reminders(current):
        slug = row.payment_method_slug
        model = _PAYMENT_MODELS.get(slug)
        if model is None:
            clear_unbound_payment_reminder(
                payment_method_slug=slug, payment_id=row.payment_id
            )
            continue
        with get_db() as session:
            payment = session.get(model, int(row.payment_id))
            if (
                payment is None
                or getattr(payment, "is_test", False)
                or getattr(payment, "telegram_chat_id", None) is not None
            ):
                clear_inside = True
                text = None
            else:
                clear_inside = False
                try:
                    titles = _candidate_titles(session, payment, slug)
                except Exception:
                    logger.warning(
                        "unbound_payment_reminder: candidate lookup failed method=%s payment_id=%s",
                        slug,
                        row.payment_id,
                        exc_info=True,
                    )
                    titles = []
                text = format_unbound_payment_reminder(
                    payment,
                    method_slug=slug,
                    waiting_minutes_value=waiting_minutes(row.created_at, current),
                    candidate_titles=titles,
                )
            session.expunge_all()
        if clear_inside:
            clear_unbound_payment_reminder(
                payment_method_slug=slug, payment_id=row.payment_id
            )
            continue
        if not text:
            continue
        ok = await notify_slack_escalation(text, source=SLACK_SOURCE)
        if not ok:
            logger.warning(
                "unbound_payment_reminder: slack failed method=%s payment_id=%s",
                slug,
                row.payment_id,
            )
            continue
        with get_db() as session:
            reminder = session.get(UnboundPaymentReminder, int(row.id))
            if reminder is not None:
                reminder.last_reminded_at = current
        sent += 1
        logger.info(
            "unbound_payment_reminder: sent method=%s payment_id=%s",
            slug,
            row.payment_id,
        )
    return sent


async def unbound_payment_reminder_job_callback(context) -> None:
    del context
    try:
        sent = await send_due_unbound_reminders()
        if sent:
            logger.info("unbound_payment_reminder job sent count=%s", sent)
    except Exception:
        logger.exception("unbound_payment_reminder job failed")


def schedule_unbound_payment_reminder_job(app) -> None:
    if app.job_queue is None:
        logger.warning(
            "unbound_payment_reminder: job_queue unavailable; reminders disabled"
        )
        return
    app.job_queue.run_repeating(
        unbound_payment_reminder_job_callback,
        interval=JOB_POLL_INTERVAL,
        first=JOB_POLL_INTERVAL,
        name="unbound_payment_reminders",
    )
    logger.info(
        "unbound_payment_reminder job scheduled interval_sec=%s",
        int(JOB_POLL_INTERVAL.total_seconds()),
    )
