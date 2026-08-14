"""Read API for persisted support-group Slack escalations."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.schemas_escalation import (
    EscalationEpisodeRead,
    EscalationEventListResponse,
    EscalationEventRead,
)
from db.connection import get_db_dependency
from db.models import EscalationEpisode, EscalationEvent

router = APIRouter(
    prefix="/api/escalations",
    tags=["escalations"],
    dependencies=[Depends(get_current_admin)],
)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
_MIGRATION_HINT = (
    "Run: python migrate_escalation_observability.py (or heroku run … on the web dyno)"
)


def _parse_dt(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        if len(raw) == 10:
            return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), _MAX_LIMIT))


def _raise_db_schema_error(exc: ProgrammingError) -> None:
    msg = str(exc.orig) if exc.orig else str(exc)
    if "escalation_" in msg and "does not exist" in msg:
        raise HTTPException(
            503, f"Escalation observability tables are missing. {_MIGRATION_HINT}"
        )


def _event_read(row: EscalationEvent) -> EscalationEventRead:
    triggers = row.trigger_messages if isinstance(row.trigger_messages, list) else []
    return EscalationEventRead(
        id=int(row.id),
        created_at=row.created_at,
        reason=row.reason,
        club_id=int(row.club_id) if row.club_id is not None else None,
        telegram_chat_id=int(row.telegram_chat_id),
        group_title=row.group_title,
        episode_id=row.episode_id,
        slack_ok=bool(row.slack_ok),
        head_admin_fanout=bool(row.head_admin_fanout),
        method_slug=row.method_slug,
        trigger_messages=list(triggers),
    )


@router.get("/events", response_model=EscalationEventListResponse)
def list_escalation_events(
    club_id: int | None = Query(None),
    chat_id: int | None = Query(None),
    reason: str | None = Query(None),
    episode_id: UUID | None = Query(None),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    limit: int = Query(_DEFAULT_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db_dependency),
):
    dt_from = _parse_dt(from_dt)
    dt_to = _parse_dt(to_dt)
    limit = _clamp_limit(limit)
    try:
        q = db.query(EscalationEvent)
        if club_id is not None:
            q = q.filter(EscalationEvent.club_id == club_id)
        if chat_id is not None:
            q = q.filter(EscalationEvent.telegram_chat_id == chat_id)
        if reason and reason.strip():
            q = q.filter(EscalationEvent.reason == reason.strip())
        if episode_id is not None:
            q = q.filter(EscalationEvent.episode_id == episode_id)
        if dt_from is not None:
            q = q.filter(EscalationEvent.created_at >= dt_from)
        if dt_to is not None:
            q = q.filter(EscalationEvent.created_at <= dt_to)
        total = q.count()
        rows = (
            q.order_by(EscalationEvent.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)
        raise
    return EscalationEventListResponse(
        items=[_event_read(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/episodes/{episode_id}", response_model=EscalationEpisodeRead)
def get_escalation_episode(
    episode_id: UUID,
    db: Session = Depends(get_db_dependency),
):
    try:
        row = db.get(EscalationEpisode, episode_id)
        if row is None:
            raise HTTPException(404, "Episode not found")
        events = (
            db.query(EscalationEvent)
            .filter(EscalationEvent.episode_id == episode_id)
            .order_by(EscalationEvent.created_at.asc())
            .all()
        )
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)
        raise
    triggers = row.trigger_messages if isinstance(row.trigger_messages, list) else []
    return EscalationEpisodeRead(
        id=row.id,
        telegram_chat_id=int(row.telegram_chat_id),
        club_id=int(row.club_id) if row.club_id is not None else None,
        group_title=row.group_title,
        opened_at=row.opened_at,
        closed_at=row.closed_at,
        close_reason=row.close_reason,
        trigger_messages=list(triggers),
        events=[_event_read(e) for e in events],
    )
