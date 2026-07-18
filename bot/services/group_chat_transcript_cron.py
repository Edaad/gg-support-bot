"""Worker JobQueue cron: nightly T+1 group-chat transcript extraction."""

from __future__ import annotations

import logging
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from club_gc_settings import is_group_transcript_cron_enabled
from bot.services.group_chat_analysis import analyze_with_retries
from bot.services.group_chat_transcript_fetch import (
    fetch_with_retries,
    previous_et_activity_date,
)
from bot.services.slack_ops_notify import notify_slack_issue_report

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_CRON_HOUR = 3
_CRON_MINUTE = 0
_BUDGET_SECONDS = 30 * 60
_ANALYSIS_BUDGET_SECONDS = 30 * 60
_JOB_NAME = "group_chat_transcript_extraction"
_SLACK_TAGS = ["account_managers"]

_START_SLACK_BODY = (
    "Nightly group-chat transcript extraction is starting.\n\n"
    "MTProto on the support bot will be paused until this finishes. "
    "Until then, /add, /cash, and automatic /gc (when a player DMs the club "
    "account) will not work.\n\n"
    "This is planned maintenance — no action needed unless extraction fails "
    "to complete."
)


def _done_slack_body(
    *,
    activity_date,
    complete: int,
    failed: int,
    timed_out: int,
) -> str:
    return (
        "Nightly group-chat transcript extraction finished.\n\n"
        f"Extracted day (America/New_York): {activity_date.isoformat()}\n"
        f"Complete: {complete}\n"
        f"Failed: {failed}\n"
        f"Timed out: {timed_out}\n\n"
        "/add, /cash, and automatic /gc are available again."
    )


async def run_group_chat_transcript_extraction(
    *,
    bot_token: str,
    chat_id: int | None = None,
    club_id: int | None = None,
    budget_seconds: float = _BUDGET_SECONDS,
) -> dict:
    """Pause MTProto, extract previous ET day transcripts, resume, notify Slack.

    Always resumes the listener in ``finally``. Optional ``chat_id`` / ``club_id``
    for one-group validation.
    """

    from bot.services.mtproto_dm_gc_listener import (
        set_planned_mtproto_pause,
        start_listener_background,
        stop_listener_background,
    )

    activity_date = previous_et_activity_date()
    summary_dict = {
        "activity_date": activity_date.isoformat(),
        "complete": 0,
        "failed": 0,
        "timed_out": 0,
    }

    await notify_slack_issue_report(_START_SLACK_BODY, tags=_SLACK_TAGS)

    set_planned_mtproto_pause(True)
    try:
        stop_listener_background()
        summary = await fetch_with_retries(
            activity_date,
            chat_id=chat_id,
            club_id=club_id,
            budget_seconds=budget_seconds,
        )
        summary_dict = {
            "activity_date": summary.activity_date.isoformat(),
            "complete": summary.complete,
            "failed": summary.failed,
            "timed_out": summary.timed_out,
        }
    except Exception:
        logger.exception(
            "group_transcript_cron: extraction crashed activity_date=%s",
            activity_date,
        )
        summary_dict["failed"] = max(int(summary_dict["failed"]), 1)
    finally:
        try:
            start_listener_background(bot_token)
        except Exception:
            logger.exception("group_transcript_cron: failed to resume MTProto listener")
        set_planned_mtproto_pause(False)

    await notify_slack_issue_report(
        _done_slack_body(
            activity_date=activity_date,
            complete=int(summary_dict["complete"]),
            failed=int(summary_dict["failed"]),
            timed_out=int(summary_dict["timed_out"]),
        ),
        tags=_SLACK_TAGS,
    )
    logger.info(
        "group_transcript_cron: extract done activity_date=%s complete=%s failed=%s timed_out=%s",
        summary_dict["activity_date"],
        summary_dict["complete"],
        summary_dict["failed"],
        summary_dict["timed_out"],
    )

    # Analysis does not need MTProto — run after resume + Slack done notice.
    analysis_dict = {
        "complete": 0,
        "failed": 0,
        "timed_out": 0,
    }
    try:
        analysis = await analyze_with_retries(
            activity_date,
            chat_id=chat_id,
            club_id=club_id,
            budget_seconds=budget_seconds
            if budget_seconds != _BUDGET_SECONDS
            else _ANALYSIS_BUDGET_SECONDS,
        )
        analysis_dict = {
            "complete": analysis.complete,
            "failed": analysis.failed,
            "timed_out": analysis.timed_out,
        }
    except Exception:
        logger.exception(
            "group_transcript_cron: analysis crashed activity_date=%s",
            activity_date,
        )
        analysis_dict["failed"] = max(int(analysis_dict["failed"]), 1)

    summary_dict["analysis_complete"] = int(analysis_dict["complete"])
    summary_dict["analysis_failed"] = int(analysis_dict["failed"])
    summary_dict["analysis_timed_out"] = int(analysis_dict["timed_out"])
    logger.info(
        "group_transcript_cron: analysis done activity_date=%s complete=%s failed=%s timed_out=%s",
        summary_dict["activity_date"],
        summary_dict["analysis_complete"],
        summary_dict["analysis_failed"],
        summary_dict["analysis_timed_out"],
    )
    return summary_dict


async def group_chat_transcript_job_callback(context) -> None:
    if not is_group_transcript_cron_enabled():
        logger.info("group_transcript_cron: skipped (GROUP_TRANSCRIPT_CRON_ENABLED off)")
        return

    bot = getattr(context, "bot", None)
    token = getattr(bot, "token", None) if bot is not None else None
    if not token:
        logger.error("group_transcript_cron: missing bot token; aborting")
        return

    try:
        await run_group_chat_transcript_extraction(bot_token=token)
    except Exception:
        logger.exception("group_transcript_cron: job callback failed")


def schedule_group_chat_transcript_job(app) -> None:
    """Register the 3:00 AM America/New_York daily JobQueue job."""

    if app.job_queue is None:
        logger.warning("group_transcript_cron: job_queue unavailable; not scheduled")
        return

    if not is_group_transcript_cron_enabled():
        logger.info(
            "group_transcript_cron: not scheduled (GROUP_TRANSCRIPT_CRON_ENABLED off)"
        )
        return

    for job in app.job_queue.get_jobs_by_name(_JOB_NAME):
        job.schedule_removal()

    app.job_queue.run_daily(
        group_chat_transcript_job_callback,
        time=dt_time(hour=_CRON_HOUR, minute=_CRON_MINUTE, tzinfo=_ET),
        name=_JOB_NAME,
    )
    logger.info(
        "group_transcript_cron: scheduled daily at %02d:%02d America/New_York",
        _CRON_HOUR,
        _CRON_MINUTE,
    )
