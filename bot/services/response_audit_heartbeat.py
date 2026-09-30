"""09:00 ET JobQueue check that the external response-audit judge ran.

If yesterday (ET) has candidate response events but no verdicts, post
"Response audit for {date} has not run." to the ops Slack channel.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, time as dt_time
from zoneinfo import ZoneInfo

from sqlalchemy import func

from bot.services.group_chat_transcript_fetch import previous_et_activity_date
from bot.services.slack_ops_notify import notify_slack_ops

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_JOB_NAME = "response_audit_heartbeat"
_HOUR = 9
_MINUTE = 0
_SLACK_SOURCE = "response_audit_heartbeat"


def candidate_and_verdict_counts(activity_date: date) -> tuple[int, int]:
    from db.connection import get_db
    from db.models import ResponseAuditVerdict, ResponseEvent

    with get_db() as session:
        candidates = (
            session.query(func.count(ResponseEvent.id))
            .filter(
                ResponseEvent.activity_date == activity_date,
                ResponseEvent.is_candidate.is_(True),
            )
            .scalar()
        )
        verdicts = (
            session.query(func.count(ResponseAuditVerdict.id))
            .join(
                ResponseEvent,
                ResponseEvent.id == ResponseAuditVerdict.response_event_id,
            )
            .filter(ResponseEvent.activity_date == activity_date)
            .scalar()
        )
    return int(candidates or 0), int(verdicts or 0)


def heartbeat_message(activity_date: date) -> str:
    return f"Response audit for {activity_date.isoformat()} has not run."


async def check_response_audit_ran(activity_date: date | None = None) -> bool:
    """Post the ops alert when needed. Returns True when an alert was sent."""

    day = activity_date or previous_et_activity_date()
    candidates, verdicts = await asyncio.to_thread(candidate_and_verdict_counts, day)
    if candidates == 0 or verdicts > 0:
        logger.info(
            "response_audit_heartbeat: ok date=%s candidates=%s verdicts=%s",
            day,
            candidates,
            verdicts,
        )
        return False
    await notify_slack_ops(heartbeat_message(day), source=_SLACK_SOURCE)
    return True


async def response_audit_heartbeat_callback(context) -> None:
    del context
    try:
        await check_response_audit_ran()
    except Exception:
        logger.exception("response_audit_heartbeat: check failed")


def schedule_response_audit_heartbeat_job(app) -> None:
    """Register the 09:00 America/New_York daily heartbeat."""

    if app.job_queue is None:
        logger.warning("response_audit_heartbeat: job_queue unavailable")
        return
    for job in app.job_queue.get_jobs_by_name(_JOB_NAME):
        job.schedule_removal()
    app.job_queue.run_daily(
        response_audit_heartbeat_callback,
        time=dt_time(hour=_HOUR, minute=_MINUTE, tzinfo=_ET),
        name=_JOB_NAME,
    )
    logger.info(
        "response_audit_heartbeat: scheduled daily at %02d:%02d America/New_York",
        _HOUR,
        _MINUTE,
    )
