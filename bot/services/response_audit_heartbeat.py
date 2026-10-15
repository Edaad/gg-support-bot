"""09:00 ET JobQueue check that yesterday's response audit is whole.

Posts to the ops Slack channel when, for yesterday (ET):

- a club's group-chat transcripts are missing (none complete, or more than
  10% of its active chats), since the audit only covers fetched chats;
- the builder recorded no run;
- there are candidate response events but no verdicts (the external judge
  did not run): "Response audit for {date} has not run."
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
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
# A club is short when more than this share of its active chats has no
# complete transcript (or none do). One stray failure a night is normal.
_MAX_MISSING_SHARE = 0.10


@dataclass(frozen=True)
class ClubCoverage:
    club_name: str
    active: int
    complete: int


@dataclass
class HeartbeatSnapshot:
    builder_ran: bool = False
    candidates: int = 0
    verdicts: int = 0
    clubs: list[ClubCoverage] = field(default_factory=list)


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


def transcript_coverage(activity_date: date) -> list[ClubCoverage]:
    """Active chats vs complete transcripts per club, by club name."""

    from db.connection import get_db
    from db.models import Club, GroupChatDailyActivity, GroupChatDailyTranscript

    with get_db() as session:
        active = (
            session.query(
                GroupChatDailyActivity.club_id, GroupChatDailyActivity.chat_id
            )
            .filter(GroupChatDailyActivity.activity_date == activity_date)
            .all()
        )
        complete_ids = {
            int(r[0])
            for r in session.query(GroupChatDailyTranscript.chat_id).filter(
                GroupChatDailyTranscript.activity_date == activity_date,
                GroupChatDailyTranscript.status == "complete",
            )
        }
        club_ids = {int(club_id) for club_id, _ in active}
        names = {
            int(cid): name
            for cid, name in session.query(Club.id, Club.name).filter(
                Club.id.in_(club_ids)
            )
        }
    by_club: dict[int, list[int]] = {}
    for club_id, chat_id in active:
        by_club.setdefault(int(club_id), []).append(int(chat_id))
    return [
        ClubCoverage(
            club_name=names.get(cid) or f"club {cid}",
            active=len(chats),
            complete=sum(1 for c in chats if c in complete_ids),
        )
        for cid, chats in sorted(by_club.items())
    ]


def builder_ran(activity_date: date) -> bool:
    from db.connection import get_db
    from db.models import ResponseAuditRun

    with get_db() as session:
        return session.get(ResponseAuditRun, activity_date) is not None


def heartbeat_snapshot(activity_date: date) -> HeartbeatSnapshot:
    candidates, verdicts = candidate_and_verdict_counts(activity_date)
    return HeartbeatSnapshot(
        builder_ran=builder_ran(activity_date),
        candidates=candidates,
        verdicts=verdicts,
        clubs=transcript_coverage(activity_date),
    )


def heartbeat_message(activity_date: date) -> str:
    return f"Response audit for {activity_date.isoformat()} has not run."


def _club_is_short(c: ClubCoverage) -> bool:
    missing = c.active - c.complete
    return c.complete == 0 or missing > c.active * _MAX_MISSING_SHARE


def heartbeat_problems(activity_date: date, snap: HeartbeatSnapshot) -> list[str]:
    day = activity_date.isoformat()
    problems: list[str] = []
    short = [c for c in snap.clubs if c.active and _club_is_short(c)]
    if short:
        detail = ", ".join(f"{c.club_name} {c.complete}/{c.active}" for c in short)
        problems.append(
            f"Group-chat transcripts for {day} are incomplete ({detail} chats "
            "fetched). The response audit only covers fetched chats."
        )
    if not snap.builder_ran:
        problems.append(f"Response audit builder for {day} has not run.")
    if snap.candidates > 0 and snap.verdicts == 0:
        problems.append(heartbeat_message(activity_date))
    return problems


async def check_response_audit_ran(activity_date: date | None = None) -> bool:
    """Post the ops alert when needed. Returns True when an alert was sent."""

    day = activity_date or previous_et_activity_date()
    snap = await asyncio.to_thread(heartbeat_snapshot, day)
    problems = heartbeat_problems(day, snap)
    if not problems:
        logger.info(
            "response_audit_heartbeat: ok date=%s candidates=%s verdicts=%s",
            day,
            snap.candidates,
            snap.verdicts,
        )
        return False
    await notify_slack_ops("\n".join(problems), source=_SLACK_SOURCE)
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
