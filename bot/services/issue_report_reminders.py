"""Slack reminders for unresolved issue reports."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from db.connection import get_db
from bot.services.issue_reports import (
    REMINDER_INTERVAL_HOURS,
    format_reminder_slack_body,
    list_open_reports_needing_reminder,
)
from bot.services.slack_ops_notify import (
    notify_slack_issue_report,
    notify_slack_issue_report_thread,
)

logger = logging.getLogger(__name__)


async def send_issue_report_reminders() -> int:
    """Post Slack reminders for open tickets due for ping. Returns count sent."""

    sent = 0
    with get_db() as session:
        reports = list_open_reports_needing_reminder(session)
        if not reports:
            return 0

        now = datetime.now(timezone.utc)
        for report in reports:
            body = format_reminder_slack_body(report)
            tags = list(report.notify_tags or [])
            if report.slack_message_ts:
                ok = await notify_slack_issue_report_thread(
                    body,
                    thread_ts=report.slack_message_ts,
                    tags=tags,
                )
            else:
                ok, message_ts, _ = await notify_slack_issue_report(
                    body,
                    tags=tags,
                )
                if message_ts and not report.slack_message_ts:
                    report.slack_message_ts = message_ts

            if ok:
                report.last_slack_reminder_at = now
                sent += 1
                logger.info(
                    "issue_report_reminder: sent report_id=%s title=%r",
                    report.id,
                    report.title,
                )
            else:
                logger.warning(
                    "issue_report_reminder: failed report_id=%s",
                    report.id,
                )

    return sent


async def issue_report_reminder_job_callback(context) -> None:
    try:
        sent = await send_issue_report_reminders()
        if sent:
            logger.info("issue_report_reminder job sent count=%s", sent)
    except Exception:
        logger.exception("issue_report_reminder job failed")


def schedule_issue_report_reminder_job(app) -> None:
    if app.job_queue is None:
        logger.warning("issue_report_reminder: job_queue unavailable; reminders disabled")
        return

    app.job_queue.run_repeating(
        issue_report_reminder_job_callback,
        interval=timedelta(hours=REMINDER_INTERVAL_HOURS),
        first=timedelta(minutes=10),
        name="issue_report_reminders",
    )
    logger.info(
        "issue_report_reminder job scheduled interval_hours=%s",
        REMINDER_INTERVAL_HOURS,
    )
