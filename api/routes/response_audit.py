"""Response audit read/write API for the external daily judge.

Auth: header ``X-Audit-Token`` must equal ``RESPONSE_AUDIT_TOKEN`` (constant-time
compare). ``503`` when the env var is unset. Not the dashboard JWT.
"""

from __future__ import annotations

import hmac
import logging
import math
import os
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.connection import get_db_dependency
from db.models import (
    AuditShift,
    GroupChatDailyActivity,
    GroupChatDailyTranscript,
    ResponseAuditRun,
    ResponseAuditVerdict,
    ResponseEvent,
)

logger = logging.getLogger(__name__)

TOKEN_ENV = "RESPONSE_AUDIT_TOKEN"
TOKEN_HEADER = "X-Audit-Token"

_ET = ZoneInfo("America/New_York")
_PT = ZoneInfo("America/Los_Angeles")
_FAST_SECONDS = 120
# An event belongs to the shift on duty at clock start + 5 min (its SLA deadline).
SHIFT_DEADLINE = timedelta(minutes=5)
UNATTRIBUTED_AGENT = "Unattributed"

Verdict = Literal["BREACH", "EXCUSED", "NOT_A_TRIGGER", "OWNER_COVER", "NEEDS_REVIEW"]


def require_audit_token(
    x_audit_token: Optional[str] = Header(None, alias=TOKEN_HEADER),
) -> None:
    expected = (os.getenv(TOKEN_ENV) or "").strip()
    if not expected:
        logger.error("response_audit api: %s not configured", TOKEN_ENV)
        raise HTTPException(503, f"{TOKEN_ENV} is not configured on the server")
    supplied = (x_audit_token or "").strip()
    if not supplied or not hmac.compare_digest(
        supplied.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(401, "Invalid audit token")


router = APIRouter(
    prefix="/api/response-audit",
    tags=["response-audit"],
    dependencies=[Depends(require_audit_token)],
)


# ── Schemas ─────────────────────────────────────────────────────────────────


class TranscriptFailure(BaseModel):
    chat_id: int
    club_id: int
    error: Optional[str] = None
    attempt_count: int = 0


class StatusResponse(BaseModel):
    date: date
    active_chats: int
    transcripts_complete: int
    transcripts_failed: list[TranscriptFailure]
    transcripts_pending: int
    events: int
    candidates: int
    verdicts: int
    builder_ran_at: Optional[datetime] = None


class VerdictRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    response_event_id: int
    verdict: str
    reason_code: Optional[str] = None
    summary: Optional[str] = None
    sling_user_id: Optional[int] = None
    agent_name: Optional[str] = None
    judge_version: Optional[str] = None
    disputed: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    activity_date: date
    chat_id: int
    club_id: int
    group_title: Optional[str] = None
    start_kind: str
    clock_start_at: datetime
    clock_start_msg_id: Optional[int] = None
    escalation_event_id: Optional[int] = None
    clock_stop_at: Optional[datetime] = None
    clock_stop_msg_id: Optional[int] = None
    responder_telegram_user_id: Optional[int] = None
    response_seconds: Optional[int] = None
    is_candidate: bool
    pre_labels: dict[str, Any] = Field(default_factory=dict)
    excerpt: Optional[list[dict[str, Any]]] = None
    rule_version: str
    created_at: Optional[datetime] = None
    verdict: Optional[VerdictRead] = None


class VerdictWrite(BaseModel):
    response_event_id: int
    verdict: Verdict
    reason_code: Optional[str] = None
    summary: Optional[str] = None
    sling_user_id: Optional[int] = None
    agent_name: Optional[str] = None
    judge_version: Optional[str] = None


class VerdictBatch(BaseModel):
    verdicts: list[VerdictWrite] = Field(..., max_length=5000)


class VerdictUpsertResponse(BaseModel):
    received: int
    created: int
    updated: int
    missing_event_ids: list[int]


class ShiftWrite(BaseModel):
    sling_shift_id: str = Field(..., min_length=1)
    sling_user_id: Optional[int] = None
    agent_name: Optional[str] = None
    starts_at: datetime
    ends_at: datetime
    label: Optional[str] = None


class ShiftBatch(BaseModel):
    shifts: list[ShiftWrite] = Field(..., max_length=5000)


class ShiftUpsertResponse(BaseModel):
    received: int
    created: int
    updated: int
    deleted: int
    range_start: Optional[datetime] = None
    range_end: Optional[datetime] = None


class BreachRead(BaseModel):
    response_event_id: int
    date: date
    time_et: str
    group_title: Optional[str] = None
    response_seconds: Optional[int] = None
    summary: Optional[str] = None


class AgentWeekStats(BaseModel):
    agent_name: str
    triggers: int
    unanswered: int
    median_seconds: Optional[float] = None
    p90_seconds: Optional[float] = None
    pct_under_120s: Optional[float] = None
    breaches: list[BreachRead]


class WeeklyResponse(BaseModel):
    week_start: date
    week_end: date
    timezone: str
    agents: list[AgentWeekStats]
    overall: AgentWeekStats


# ── Helpers ─────────────────────────────────────────────────────────────────


def _parse_day(raw: str, *, name: str) -> date:
    try:
        return date.fromisoformat((raw or "").strip()[:10])
    except ValueError as exc:
        raise HTTPException(400, f"Invalid {name}: {raw!r}") from exc


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _percentile(sorted_values: list[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile; ``inf`` (unanswered) propagates as None."""

    if not sorted_values:
        return None
    rank = max(1, math.ceil(pct / 100.0 * len(sorted_values)))
    value = sorted_values[rank - 1]
    return None if math.isinf(value) else float(value)


def _median(sorted_values: list[float]) -> Optional[float]:
    if not sorted_values:
        return None
    n = len(sorted_values)
    mid = n // 2
    if n % 2:
        value = sorted_values[mid]
    else:
        value = (sorted_values[mid - 1] + sorted_values[mid]) / 2
    return None if math.isinf(value) else float(value)


def _week_stats(
    name: str,
    rows: list[tuple[ResponseEvent, Optional[ResponseAuditVerdict]]],
) -> AgentWeekStats:
    values = sorted(
        float(ev.response_seconds) if ev.response_seconds is not None else math.inf
        for ev, _v in rows
    )
    triggers = len(rows)
    fast = sum(1 for v in values if v <= _FAST_SECONDS)
    breaches: list[BreachRead] = []
    for ev, verdict in rows:
        if verdict is None or verdict.verdict != "BREACH":
            continue
        start_et = _as_utc(ev.clock_start_at).astimezone(_ET)
        breaches.append(
            BreachRead(
                response_event_id=int(ev.id),
                date=ev.activity_date,
                time_et=start_et.strftime("%H:%M"),
                group_title=ev.group_title,
                response_seconds=ev.response_seconds,
                summary=verdict.summary,
            )
        )
    breaches.sort(key=lambda b: (b.date, b.time_et))
    return AgentWeekStats(
        agent_name=name,
        triggers=triggers,
        unanswered=sum(1 for v in values if math.isinf(v)),
        median_seconds=_median(values),
        p90_seconds=_percentile(values, 90),
        pct_under_120s=(round(100.0 * fast / triggers, 1) if triggers else None),
        breaches=breaches,
    )


def _shift_agents(
    deadline: datetime, shifts: list[tuple[datetime, datetime, str]]
) -> list[str]:
    """Agents whose shift covers ``deadline`` (``starts_at <= t < ends_at``).

    A deadline exactly at a shift's end belongs to the next shift. Overlapping
    shifts credit every agent on duty.
    """

    names: list[str] = []
    for starts, ends, name in shifts:
        if starts <= deadline < ends and name not in names:
            names.append(name)
    return names


# ── Routes ──────────────────────────────────────────────────────────────────


@router.get("/status", response_model=StatusResponse)
def get_status(
    date_param: str = Query(..., alias="date", description="YYYY-MM-DD (ET day)"),
    db: Session = Depends(get_db_dependency),
):
    day = _parse_day(date_param, name="date")
    active_ids = {
        int(r[0])
        for r in db.query(GroupChatDailyActivity.chat_id)
        .filter(GroupChatDailyActivity.activity_date == day)
        .all()
    }
    transcripts = (
        db.query(GroupChatDailyTranscript)
        .filter(GroupChatDailyTranscript.activity_date == day)
        .all()
    )
    complete = [t for t in transcripts if t.status == "complete"]
    failed = [t for t in transcripts if t.status == "failed"]
    pending_rows = [t for t in transcripts if t.status == "pending"]
    with_row = {int(t.chat_id) for t in transcripts}
    pending = len(pending_rows) + len(active_ids - with_row)

    events = (
        db.query(func.count(ResponseEvent.id))
        .filter(ResponseEvent.activity_date == day)
        .scalar()
    )
    candidates = (
        db.query(func.count(ResponseEvent.id))
        .filter(
            ResponseEvent.activity_date == day,
            ResponseEvent.is_candidate.is_(True),
        )
        .scalar()
    )
    verdicts = (
        db.query(func.count(ResponseAuditVerdict.id))
        .join(ResponseEvent, ResponseEvent.id == ResponseAuditVerdict.response_event_id)
        .filter(ResponseEvent.activity_date == day)
        .scalar()
    )
    run = db.get(ResponseAuditRun, day)
    return StatusResponse(
        date=day,
        active_chats=len(active_ids),
        transcripts_complete=len(complete),
        transcripts_failed=[
            TranscriptFailure(
                chat_id=int(t.chat_id),
                club_id=int(t.club_id),
                error=t.error,
                attempt_count=int(t.attempt_count or 0),
            )
            for t in sorted(failed, key=lambda t: int(t.chat_id))
        ],
        transcripts_pending=pending,
        events=int(events or 0),
        candidates=int(candidates or 0),
        verdicts=int(verdicts or 0),
        builder_ran_at=run.ran_at if run is not None else None,
    )


@router.get("/candidates", response_model=list[CandidateRead])
def list_candidates(
    date_param: str = Query(..., alias="date", description="YYYY-MM-DD (ET day)"),
    unjudged: bool = Query(False, description="Only events without a verdict"),
    db: Session = Depends(get_db_dependency),
):
    day = _parse_day(date_param, name="date")
    q = (
        db.query(ResponseEvent, ResponseAuditVerdict)
        .outerjoin(
            ResponseAuditVerdict,
            ResponseAuditVerdict.response_event_id == ResponseEvent.id,
        )
        .filter(
            ResponseEvent.activity_date == day,
            ResponseEvent.is_candidate.is_(True),
        )
    )
    if unjudged:
        q = q.filter(ResponseAuditVerdict.id.is_(None))
    rows = q.order_by(ResponseEvent.clock_start_at, ResponseEvent.id).all()
    out: list[CandidateRead] = []
    for ev, verdict in rows:
        item = CandidateRead.model_validate(ev, from_attributes=True)
        item.pre_labels = ev.pre_labels if isinstance(ev.pre_labels, dict) else {}
        item.verdict = (
            VerdictRead.model_validate(verdict) if verdict is not None else None
        )
        out.append(item)
    return out


@router.post("/verdicts", response_model=VerdictUpsertResponse)
def upsert_verdicts(
    body: VerdictBatch,
    db: Session = Depends(get_db_dependency),
):
    """Upsert by ``response_event_id``. ``disputed`` is never changed here."""

    latest: dict[int, VerdictWrite] = {}
    for item in body.verdicts:
        latest[int(item.response_event_id)] = item
    ids = sorted(latest)
    known = {
        int(r[0])
        for r in db.query(ResponseEvent.id).filter(ResponseEvent.id.in_(ids)).all()
    }
    existing = {
        int(v.response_event_id): v
        for v in db.query(ResponseAuditVerdict)
        .filter(ResponseAuditVerdict.response_event_id.in_(ids))
        .all()
    }
    created = updated = 0
    now = datetime.now(timezone.utc)
    for event_id in ids:
        if event_id not in known:
            continue
        item = latest[event_id]
        row = existing.get(event_id)
        if row is None:
            row = ResponseAuditVerdict(response_event_id=event_id)
            db.add(row)
            created += 1
        else:
            row.updated_at = now
            updated += 1
        row.verdict = item.verdict
        row.reason_code = item.reason_code
        row.summary = item.summary
        row.sling_user_id = item.sling_user_id
        row.agent_name = item.agent_name
        row.judge_version = item.judge_version
    db.flush()
    return VerdictUpsertResponse(
        received=len(body.verdicts),
        created=created,
        updated=updated,
        missing_event_ids=[i for i in ids if i not in known],
    )


@router.get("/weekly", response_model=WeeklyResponse)
def weekly_report(
    week_start: str = Query(..., description="Monday YYYY-MM-DD (Pacific week)"),
    db: Session = Depends(get_db_dependency),
):
    """Per-agent response stats for one Pacific (Mon–Sun) week.

    Events are placed in a week by ``clock_start_at`` in America/Los_Angeles and
    attributed to the ``audit_shifts`` agent on duty at clock start + 5 min
    (``starts_at <= t < ends_at``; overlapping shifts credit each agent).
    Events with no shift are ``Unattributed``. ``NOT_A_TRIGGER`` verdicts and
    bot-resolved events are excluded. Unanswered events count as slower than any
    answered one in the median / p90 (which read ``null`` when they land on an
    unanswered event). ``overall`` counts each event once.
    """

    start_day = _parse_day(week_start, name="week_start")
    if start_day.weekday() != 0:
        raise HTTPException(400, "week_start must be a Monday")
    start_pt = datetime.combine(start_day, dt_time.min, tzinfo=_PT)
    end_pt = datetime.combine(start_day + timedelta(days=7), dt_time.min, tzinfo=_PT)
    start_utc = start_pt.astimezone(timezone.utc)
    end_utc = end_pt.astimezone(timezone.utc)

    rows = (
        db.query(ResponseEvent, ResponseAuditVerdict)
        .outerjoin(
            ResponseAuditVerdict,
            ResponseAuditVerdict.response_event_id == ResponseEvent.id,
        )
        .filter(
            ResponseEvent.clock_start_at >= start_utc,
            ResponseEvent.clock_start_at < end_utc,
        )
        .order_by(ResponseEvent.clock_start_at, ResponseEvent.id)
        .all()
    )
    kept = [
        (ev, v)
        for ev, v in rows
        if (v is None or v.verdict != "NOT_A_TRIGGER")
        and not (isinstance(ev.pre_labels, dict) and ev.pre_labels.get("bot_resolved"))
    ]
    shifts = [
        (
            _as_utc(sh.starts_at),
            _as_utc(sh.ends_at),
            (sh.agent_name or "").strip() or f"sling:{sh.sling_user_id}",
        )
        for sh in db.query(AuditShift)
        .filter(
            AuditShift.ends_at > start_utc,
            AuditShift.starts_at < end_utc + SHIFT_DEADLINE,
        )
        .all()
    ]
    by_agent: dict[str, list[tuple[ResponseEvent, Optional[ResponseAuditVerdict]]]]
    by_agent = {}
    for ev, v in kept:
        deadline = _as_utc(ev.clock_start_at) + SHIFT_DEADLINE
        for name in _shift_agents(deadline, shifts) or [UNATTRIBUTED_AGENT]:
            by_agent.setdefault(name, []).append((ev, v))
    agents = [_week_stats(name, items) for name, items in by_agent.items()]
    agents.sort(key=lambda a: (a.agent_name == UNATTRIBUTED_AGENT, a.agent_name))
    return WeeklyResponse(
        week_start=start_day,
        week_end=start_day + timedelta(days=6),
        timezone="America/Los_Angeles",
        agents=agents,
        overall=_week_stats("ALL", kept),
    )


@router.post("/shifts", response_model=ShiftUpsertResponse)
def upsert_shifts(
    body: ShiftBatch,
    db: Session = Depends(get_db_dependency),
):
    """Upsert Sling shifts by ``sling_shift_id``.

    The payload is the full schedule for the range it covers
    (earliest ``starts_at`` → latest ``ends_at``): stored shifts starting in that
    range that are missing from the payload are deleted (swaps / removals).
    """

    latest: dict[str, ShiftWrite] = {}
    for item in body.shifts:
        if _as_utc(item.ends_at) <= _as_utc(item.starts_at):
            raise HTTPException(
                400, f"shift {item.sling_shift_id}: ends_at must be after starts_at"
            )
        latest[item.sling_shift_id] = item
    if not latest:
        return ShiftUpsertResponse(received=0, created=0, updated=0, deleted=0)

    range_start = min(_as_utc(i.starts_at) for i in latest.values())
    range_end = max(_as_utc(i.ends_at) for i in latest.values())
    existing = {
        row.sling_shift_id: row
        for row in db.query(AuditShift)
        .filter(AuditShift.sling_shift_id.in_(sorted(latest)))
        .all()
    }
    created = updated = 0
    now = datetime.now(timezone.utc)
    for shift_id, item in latest.items():
        row = existing.get(shift_id)
        if row is None:
            row = AuditShift(sling_shift_id=shift_id)
            db.add(row)
            created += 1
        else:
            updated += 1
        row.sling_user_id = item.sling_user_id
        row.agent_name = item.agent_name
        row.starts_at = _as_utc(item.starts_at)
        row.ends_at = _as_utc(item.ends_at)
        row.label = item.label
        row.updated_at = now
    db.flush()

    stale = (
        db.query(AuditShift)
        .filter(
            AuditShift.starts_at >= range_start,
            AuditShift.starts_at < range_end,
            AuditShift.sling_shift_id.notin_(sorted(latest)),
        )
        .all()
    )
    for row in stale:
        db.delete(row)
    db.flush()
    return ShiftUpsertResponse(
        received=len(body.shifts),
        created=created,
        updated=updated,
        deleted=len(stale),
        range_start=range_start,
        range_end=range_end,
    )
