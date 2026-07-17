"""JWT-protected REST for daily group-chat activity rollups and transcripts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from db.connection import get_db_dependency
from db.models import GroupChatDailyActivity, GroupChatDailyTranscript

router = APIRouter(
    tags=["group-chat-activity"],
    dependencies=[Depends(get_current_admin)],
)


class GroupChatDailyActivityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    activity_date: date
    chat_id: int
    club_id: int
    non_bot_message_count: int
    first_message_at: datetime
    last_message_at: datetime
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GroupChatTranscriptMetaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    activity_date: date
    chat_id: int
    club_id: int
    status: str
    message_count: int
    error: str | None = None
    attempt_count: int
    fetched_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GroupChatTranscriptDetailRead(GroupChatTranscriptMetaRead):
    messages: list[dict[str, Any]] | None = None


def _parse_activity_date(raw: str) -> date:
    text = (raw or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid activity_date: {raw!r}") from exc


@router.get(
    "/api/group-chat-daily-activity",
    response_model=List[GroupChatDailyActivityRead],
)
def list_group_chat_daily_activity(
    activity_date: str = Query(..., description="YYYY-MM-DD (America/New_York day)"),
    club_id: Optional[int] = Query(None),
    db: Session = Depends(get_db_dependency),
):
    day = _parse_activity_date(activity_date)
    q = db.query(GroupChatDailyActivity).filter(
        GroupChatDailyActivity.activity_date == day
    )
    if club_id is not None:
        q = q.filter(GroupChatDailyActivity.club_id == int(club_id))
    rows = q.order_by(
        GroupChatDailyActivity.club_id,
        GroupChatDailyActivity.chat_id,
    ).all()
    return [GroupChatDailyActivityRead.model_validate(r) for r in rows]


@router.get(
    "/api/group-chat-transcripts",
    response_model=List[GroupChatTranscriptMetaRead],
)
def list_group_chat_transcripts(
    activity_date: str = Query(..., description="YYYY-MM-DD (America/New_York day)"),
    club_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db_dependency),
):
    day = _parse_activity_date(activity_date)
    q = db.query(GroupChatDailyTranscript).filter(
        GroupChatDailyTranscript.activity_date == day
    )
    if club_id is not None:
        q = q.filter(GroupChatDailyTranscript.club_id == int(club_id))
    if status is not None:
        status_norm = status.strip().lower()
        if status_norm not in ("pending", "complete", "failed"):
            raise HTTPException(
                400, "status must be one of: pending, complete, failed"
            )
        q = q.filter(GroupChatDailyTranscript.status == status_norm)
    rows = q.order_by(
        GroupChatDailyTranscript.club_id,
        GroupChatDailyTranscript.chat_id,
    ).all()
    return [GroupChatTranscriptMetaRead.model_validate(r) for r in rows]


@router.get(
    "/api/group-chat-transcripts/{chat_id}",
    response_model=GroupChatTranscriptDetailRead,
)
def get_group_chat_transcript(
    chat_id: int,
    activity_date: str = Query(..., description="YYYY-MM-DD (America/New_York day)"),
    db: Session = Depends(get_db_dependency),
):
    day = _parse_activity_date(activity_date)
    row = (
        db.query(GroupChatDailyTranscript)
        .filter(
            GroupChatDailyTranscript.activity_date == day,
            GroupChatDailyTranscript.chat_id == int(chat_id),
        )
        .one_or_none()
    )
    if row is None:
        raise HTTPException(404, "Transcript not found")
    return GroupChatTranscriptDetailRead.model_validate(row)
