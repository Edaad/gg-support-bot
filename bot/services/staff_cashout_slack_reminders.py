"""5-minute head-admin Slack reminders for overdue Active staff cashouts."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import joinedload

from bot.services.staff_cashout_records import compute_ledger
from db.connection import get_db
from db.models import StaffCashoutRecord, StaffCashoutSlackReminderControl

logger = logging.getLogger(__name__)

REMINDER_INTERVAL = timedelta(minutes=5)
JOB_POLL_INTERVAL = timedelta(seconds=30)
SLACK_SOURCE = "cashout_slack_reminder"
DASHBOARD_PUBLIC_URL_ENV = "DASHBOARD_PUBLIC_URL"
EASTERN = ZoneInfo("America/New_York")
DEFAULT_HOURS_START = "08:00"
DEFAULT_HOURS_END = "23:00"
_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")


def _naive_utc(dt: datetime | None) -> datetime | None:
    """Normalize to naive UTC for comparison with staff_cashout_records.created_at."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _now_naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_notify_hhmm(value: str) -> str:
    """Normalize HH:MM (optional seconds). Raises ValueError if invalid."""
    raw = (value or "").strip()
    match = _HHMM_RE.fullmatch(raw)
    if not match:
        raise ValueError("Hours must be HH:MM")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        raise ValueError("Hours must be HH:MM")
    return f"{hour:02d}:{minute:02d}"


def _hhmm_to_minutes(value: str) -> int:
    hour, minute = (int(p) for p in parse_notify_hhmm(value).split(":"))
    return hour * 60 + minute


def is_within_est_window(
    now: datetime | None,
    hours_start: str,
    hours_end: str,
) -> bool:
    """True if `now` (UTC naive or aware) falls in [start, end) America/New_York.

    Equal start and end means 24h. If start > end the window wraps midnight.
    """
    est = _as_utc(now).astimezone(EASTERN)
    current = est.hour * 60 + est.minute
    start_m = _hhmm_to_minutes(hours_start or DEFAULT_HOURS_START)
    end_m = _hhmm_to_minutes(hours_end or DEFAULT_HOURS_END)
    if start_m == end_m:
        return True
    if start_m < end_m:
        return start_m <= current < end_m
    return current >= start_m or current < end_m


def _control_hours_start(row: StaffCashoutSlackReminderControl) -> str:
    return str(getattr(row, "hours_start", None) or DEFAULT_HOURS_START)


def _control_hours_end(row: StaffCashoutSlackReminderControl) -> str:
    return str(getattr(row, "hours_end", None) or DEFAULT_HOURS_END)


def _hours_open_for_control(
    row: StaffCashoutSlackReminderControl,
    now: datetime | None,
) -> bool:
    if not bool(getattr(row, "hours_enabled", True)):
        return True
    return is_within_est_window(
        now, _control_hours_start(row), _control_hours_end(row)
    )


def _control_to_dict(row: StaffCashoutSlackReminderControl) -> dict[str, Any]:
    return {
        "enabled": bool(row.enabled),
        "enabled_at": row.enabled_at,
        "updated_at": row.updated_at,
        "hours_enabled": bool(getattr(row, "hours_enabled", True)),
        "hours_start": _control_hours_start(row),
        "hours_end": _control_hours_end(row),
    }


def format_remaining_money(amount: Any) -> str:
    """Match dashboard fmtMoney: $ with thousands separators and 2 decimals."""
    n = Decimal(str(amount or 0))
    sign = "-" if n < 0 else ""
    abs_n = abs(n)
    return f"{sign}${abs_n:,.2f}"


def dashboard_public_base_url() -> str | None:
    raw = (os.getenv(DASHBOARD_PUBLIC_URL_ENV) or "").strip().rstrip("/")
    return raw or None


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


def format_cashout_pushover_reminder(
    *,
    group_title: str,
    remaining: Any,
    method_label: str | None = None,
) -> str:
    """Plain-text body for the 5-minute overdue Pushover alert."""
    player = (group_title or "").strip() or "(unnamed)"
    method = (method_label or "").strip() or "Other"
    return "\n".join(
        [
            "This player has been waiting longer than 5 minutes to get cashed out!",
            "",
            f"Player: {player}",
            f"Amount: {format_remaining_money(remaining)}",
            f"Tag: {method}",
        ]
    )


def format_cashout_pushover_create(
    *,
    group_title: str,
    amount: Any,
    method_label: str,
) -> str:
    """Plain-text body for a new-cashout Pushover alert."""
    player = (group_title or "").strip() or "(unnamed)"
    method = (method_label or "").strip() or "Other"
    return "\n".join(
        [
            f"Player: {player}",
            f"Amount: {format_remaining_money(amount)}",
            f"Tag: {method}",
        ]
    )


def _ensure_control_row(session) -> StaffCashoutSlackReminderControl:
    row = (
        session.query(StaffCashoutSlackReminderControl)
        .filter(StaffCashoutSlackReminderControl.id == 1)
        .first()
    )
    if row is None:
        row = StaffCashoutSlackReminderControl(
            id=1,
            enabled=False,
            hours_enabled=True,
            hours_start=DEFAULT_HOURS_START,
            hours_end=DEFAULT_HOURS_END,
        )
        session.add(row)
        session.flush()
    return row


def get_notify_control() -> dict[str, Any]:
    with get_db() as session:
        return _control_to_dict(_ensure_control_row(session))


def get_slack_reminder_enabled() -> bool:
    return bool(get_notify_control()["enabled"])


def cashout_staff_alerts_open(now: datetime | None = None) -> bool:
    """False while active-hours are enabled and `now` is outside the EST window."""
    with get_db() as session:
        row = _ensure_control_row(session)
        return _hours_open_for_control(row, now)


def set_notify_control(
    *,
    enabled: bool | None = None,
    hours_enabled: bool | None = None,
    hours_start: str | None = None,
    hours_end: str | None = None,
) -> dict[str, Any]:
    """Persist notify toggle and/or EST hours. Enabling refreshes enabled_at."""
    now = datetime.now(timezone.utc)
    start = parse_notify_hhmm(hours_start) if hours_start is not None else None
    end = parse_notify_hhmm(hours_end) if hours_end is not None else None
    with get_db() as session:
        row = _ensure_control_row(session)
        if enabled is not None:
            row.enabled = bool(enabled)
            if enabled:
                row.enabled_at = now
        if hours_enabled is not None:
            row.hours_enabled = bool(hours_enabled)
        if start is not None:
            row.hours_start = start
        if end is not None:
            row.hours_end = end
        row.updated_at = now
        session.flush()
        return _control_to_dict(row)


def set_slack_reminder_enabled(enabled: bool) -> dict[str, Any]:
    """Persist toggle. When enabling, set enabled_at so overdue rows re-fire."""
    return set_notify_control(enabled=enabled)


def mark_create_notified(record_id: int, at: datetime | None = None) -> None:
    """Stamp initial create alert so it is not deferred or retried."""
    ping_at = _naive_utc(at) if at is not None else _now_naive_utc()
    with get_db() as session:
        row = session.get(StaffCashoutRecord, int(record_id))
        if row is None or getattr(row, "create_notified_at", None) is not None:
            return
        row.create_notified_at = ping_at


def _is_due(
    *,
    created_at: datetime | None,
    last_slack_reminder_at: datetime | None,
    enabled_at: datetime | None,
    now: datetime,
    create_notified_at: datetime | None = None,
) -> bool:
    notified = _naive_utc(create_notified_at)
    if notified is None:
        return False
    if notified > now - REMINDER_INTERVAL:
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
    """Active overdue cashouts due for a 5-minute reminder (Slack and/or Pushover)."""
    now_naive = _naive_utc(now) if now is not None else _now_naive_utc()
    assert now_naive is not None
    cutoff = now_naive - REMINDER_INTERVAL

    with get_db() as session:
        control = _ensure_control_row(session)
        if not _hours_open_for_control(control, now_naive):
            return []

        enabled_at = control.enabled_at
        rows = (
            session.query(StaffCashoutRecord)
            .options(joinedload(StaffCashoutRecord.money_sends))
            .filter(
                StaffCashoutRecord.do_not_send.is_(False),
                StaffCashoutRecord.sending.is_(False),
                StaffCashoutRecord.tracks_money_sent.is_(True),
                StaffCashoutRecord.created_at.isnot(None),
                StaffCashoutRecord.created_at <= cutoff,
                StaffCashoutRecord.create_notified_at.isnot(None),
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
            if bool(getattr(record, "sending", False)):
                continue
            if not _is_due(
                created_at=record.created_at,
                last_slack_reminder_at=getattr(
                    record, "last_slack_reminder_at", None
                ),
                enabled_at=enabled_at,
                now=now_naive,
                create_notified_at=getattr(record, "create_notified_at", None),
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


def list_pending_create_notifies(
    now: datetime | None = None,
) -> list[int]:
    """Active cashouts that still need the initial create Pushover."""
    now_naive = _naive_utc(now) if now is not None else _now_naive_utc()
    assert now_naive is not None

    with get_db() as session:
        control = _ensure_control_row(session)
        if not _hours_open_for_control(control, now_naive):
            return []

        rows = (
            session.query(StaffCashoutRecord)
            .options(joinedload(StaffCashoutRecord.money_sends))
            .filter(
                StaffCashoutRecord.do_not_send.is_(False),
                StaffCashoutRecord.sending.is_(False),
                StaffCashoutRecord.tracks_money_sent.is_(True),
                StaffCashoutRecord.create_notified_at.is_(None),
            )
            .order_by(StaffCashoutRecord.created_at.asc(), StaffCashoutRecord.id.asc())
            .all()
        )
        pending: list[int] = []
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
            pending.append(int(record.id))
        return pending


def send_pending_create_notifies(now: datetime | None = None) -> int:
    """Send deferred New Cashout Pushover. Returns records attempted."""
    from bot.services.staff_cashout_pushover import (
        SOURCE_CREATE,
        notify_cashout_pushover_sync,
    )

    pending = list_pending_create_notifies(now=now)
    sent = 0
    for record_id in pending:
        notify_cashout_pushover_sync(
            record_id,
            source=SOURCE_CREATE,
        )
        sent += 1
        logger.info(
            "cashout_create_notify: deferred record_id=%s",
            record_id,
        )
    return sent


async def send_due_cashout_reminders(
    now: datetime | None = None,
) -> int:
    """Post overdue Pushover, and Slack when the 5 min Slack reminder is on."""
    from bot.services.staff_cashout_pushover import (
        SOURCE_OVERDUE,
        notify_cashout_pushover_async,
    )
    from bot.services.slack_ops_notify import notify_slack_head_admin_escalation

    send_pending_create_notifies(now=now)
    due = list_due_cashout_reminders(now=now)
    if not due:
        return 0

    slack_on = get_slack_reminder_enabled()
    base = dashboard_public_base_url()
    if not base:
        logger.warning(
            "cashout_slack_reminder: %s unset; Open cashout link omitted",
            DASHBOARD_PUBLIC_URL_ENV,
        )

    sent = 0
    ping_at = _now_naive_utc()
    for item in due:
        record_id = int(item["id"])
        group_title = str(item.get("group_title") or "")
        remaining = item.get("remaining")
        url = f"{base}/cashout-records/{record_id}" if base else None
        if slack_on:
            text = format_cashout_slack_reminder(
                group_title=group_title,
                remaining=remaining,
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

        # Method-filtered Pushover. Stamp regardless of Pushover outcome so we
        # do not re-fire every 30s poll. Slack (when on) must succeed first.
        push_sent = await notify_cashout_pushover_async(
            record_id,
            source=SOURCE_OVERDUE,
        )
        if not push_sent:
            logger.warning(
                "cashout_slack_reminder: pushover none_sent record_id=%s",
                record_id,
            )

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
            "cashout_slack_reminder: sent record_id=%s title=%r pushover_count=%s",
            record_id,
            item.get("group_title"),
            push_sent,
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
