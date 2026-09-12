"""5-minute head-admin Slack reminders for overdue Active staff cashouts."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import joinedload

from bot.services.staff_cashout_records import compute_ledger
from db.connection import get_db
from db.models import StaffCashoutRecord, StaffCashoutSlackReminderControl

logger = logging.getLogger(__name__)

REMINDER_INTERVAL = timedelta(minutes=5)
JOB_POLL_INTERVAL = timedelta(seconds=30)
SLACK_SOURCE = "cashout_slack_reminder"
DASHBOARD_PUBLIC_URL_ENV = "DASHBOARD_PUBLIC_URL"
HEROKU_APP_NAME_ENV = "HEROKU_APP_NAME"


def _naive_utc(dt: datetime | None) -> datetime | None:
    """Normalize to naive UTC for comparison with staff_cashout_records.created_at."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _now_naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def format_remaining_money(amount: Any) -> str:
    """Match dashboard fmtMoney: $ with thousands separators and 2 decimals."""
    n = Decimal(str(amount or 0))
    sign = "-" if n < 0 else ""
    abs_n = abs(n)
    return f"{sign}${abs_n:,.2f}"


def dashboard_public_base_url() -> str | None:
    raw = (os.getenv(DASHBOARD_PUBLIC_URL_ENV) or "").strip().rstrip("/")
    if raw:
        return raw
    app_name = (os.getenv(HEROKU_APP_NAME_ENV) or "").strip()
    if app_name:
        return f"https://{app_name}.herokuapp.com"
    return None


def cashout_record_dashboard_url(record_id: int) -> str | None:
    base = dashboard_public_base_url()
    if not base:
        return None
    return f"{base}/cashout-records/{int(record_id)}"


def format_cashout_slack_reminder(
    *,
    group_title: str,
    remaining: Any,
    record_id: int,
    dashboard_url: str | None = None,
) -> str:
    title = (group_title or "").strip() or "(unnamed)"
    safe_title = title.replace("`", "'")
    lines = [
        ":siren: URGENT :siren:",
        "",
        "Contact head admins immediately to cash the following player "
        "on the Hub who has been waiting longer than 5 minutes:",
        "",
        f"`{safe_title}`",
        "",
        f"Remaining: {format_remaining_money(remaining)}",
    ]
    url = dashboard_url if dashboard_url is not None else cashout_record_dashboard_url(
        record_id
    )
    if url:
        lines.append(f"<{url}|Open cashout>")
    return "\n".join(lines)


def _ensure_control_row(session) -> StaffCashoutSlackReminderControl:
    row = (
        session.query(StaffCashoutSlackReminderControl)
        .filter(StaffCashoutSlackReminderControl.id == 1)
        .first()
    )
    if row is None:
        row = StaffCashoutSlackReminderControl(id=1, enabled=False)
        session.add(row)
        session.flush()
    return row


def get_slack_reminder_enabled() -> bool:
    with get_db() as session:
        row = _ensure_control_row(session)
        return bool(row.enabled)


def set_slack_reminder_enabled(enabled: bool) -> dict[str, Any]:
    """Persist toggle. When enabling, set enabled_at so overdue rows re-fire."""
    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = _ensure_control_row(session)
        row.enabled = bool(enabled)
        if enabled:
            row.enabled_at = now
        row.updated_at = now
        session.flush()
        return {
            "enabled": bool(row.enabled),
            "enabled_at": row.enabled_at,
            "updated_at": row.updated_at,
        }


def _is_due(
    *,
    created_at: datetime | None,
    last_slack_reminder_at: datetime | None,
    enabled_at: datetime | None,
    now: datetime,
) -> bool:
    created = _naive_utc(created_at)
    if created is None:
        return False
    if created > now - REMINDER_INTERVAL:
        return False

    last = _naive_utc(last_slack_reminder_at)
    enabled = _naive_utc(enabled_at)

    if last is None:
        return True
    if enabled is not None and last < enabled:
        return True
    return last <= now - REMINDER_INTERVAL


def list_due_cashout_reminders(
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Active overdue cashouts due for a Slack ping while the control is enabled."""
    now_naive = _naive_utc(now) if now is not None else _now_naive_utc()
    assert now_naive is not None
    cutoff = now_naive - REMINDER_INTERVAL

    with get_db() as session:
        control = _ensure_control_row(session)
        if not control.enabled:
            return []

        enabled_at = control.enabled_at
        rows = (
            session.query(StaffCashoutRecord)
            .options(joinedload(StaffCashoutRecord.money_sends))
            .filter(
                StaffCashoutRecord.do_not_send.is_(False),
                StaffCashoutRecord.tracks_money_sent.is_(True),
                StaffCashoutRecord.created_at.isnot(None),
                StaffCashoutRecord.created_at <= cutoff,
            )
            .order_by(StaffCashoutRecord.created_at.asc(), StaffCashoutRecord.id.asc())
            .all()
        )

        due: list[dict[str, Any]] = []
        for record in rows:
            sends = [
                {
                    "amount": s.amount,
                    "created_at": s.created_at,
                }
                for s in (record.money_sends or [])
            ]
            ledger = compute_ledger(True, record.amount, sends)
            if ledger["status"] != "active":
                continue
            if not _is_due(
                created_at=record.created_at,
                last_slack_reminder_at=getattr(
                    record, "last_slack_reminder_at", None
                ),
                enabled_at=enabled_at,
                now=now_naive,
            ):
                continue
            due.append(
                {
                    "id": int(record.id),
                    "group_title": record.group_title or "",
                    "remaining": ledger["remaining"],
                    "created_at": record.created_at,
                    "last_slack_reminder_at": getattr(
                        record, "last_slack_reminder_at", None
                    ),
                }
            )
        return due


async def send_due_cashout_reminders(
    now: datetime | None = None,
) -> int:
    """Post head-admin Slack for each due cashout. Returns count sent."""
    from bot.services.slack_ops_notify import notify_slack_head_admin_escalation

    due = list_due_cashout_reminders(now=now)
    if not due:
        return 0

    base = dashboard_public_base_url()
    if not base:
        logger.warning(
            "cashout_slack_reminder: %s / %s unset; Open cashout link omitted",
            DASHBOARD_PUBLIC_URL_ENV,
            HEROKU_APP_NAME_ENV,
        )

    sent = 0
    ping_at = _now_naive_utc()
    for item in due:
        record_id = int(item["id"])
        url = f"{base}/cashout-records/{record_id}" if base else None
        text = format_cashout_slack_reminder(
            group_title=str(item.get("group_title") or ""),
            remaining=item.get("remaining"),
            record_id=record_id,
            dashboard_url=url,
        )
        ok = await notify_slack_head_admin_escalation(text, source=SLACK_SOURCE)
        if not ok:
            logger.warning(
                "cashout_slack_reminder: slack failed record_id=%s",
                record_id,
            )
            continue

        with get_db() as session:
            row = (
                session.query(StaffCashoutRecord)
                .filter(StaffCashoutRecord.id == record_id)
                .first()
            )
            if row is not None:
                row.last_slack_reminder_at = ping_at
        sent += 1
        logger.info(
            "cashout_slack_reminder: sent record_id=%s title=%r",
            record_id,
            item.get("group_title"),
        )

    return sent


async def cashout_slack_reminder_job_callback(context) -> None:
    try:
        sent = await send_due_cashout_reminders()
        if sent:
            logger.info("cashout_slack_reminder job sent count=%s", sent)
    except Exception:
        logger.exception("cashout_slack_reminder job failed")


def schedule_cashout_slack_reminder_job(app) -> None:
    if app.job_queue is None:
        logger.warning(
            "cashout_slack_reminder: job_queue unavailable; reminders disabled"
        )
        return

    app.job_queue.run_repeating(
        cashout_slack_reminder_job_callback,
        interval=JOB_POLL_INTERVAL,
        first=JOB_POLL_INTERVAL,
        name="staff_cashout_slack_reminders",
    )
    logger.info(
        "cashout_slack_reminder job scheduled interval_sec=%s",
        int(JOB_POLL_INTERVAL.total_seconds()),
    )
