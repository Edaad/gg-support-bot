"""JWT-protected REST for daily group-chat activity rollups and transcripts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.group_chat_ticket_helpers import (
    compute_ticket_duration,
    customer_first_from_events,
    index_messages_by_id,
    slice_ticket_messages,
)
from db.connection import get_db_dependency
from db.models import (
    Club,
    Group,
    GroupChatDailyActivity,
    GroupChatDailyTranscript,
    GroupChatTicket,
)

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
    analysis_status: str = "pending"
    analysis_error: str | None = None
    analysis_attempt_count: int = 0
    analyzed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GroupChatTranscriptDetailRead(GroupChatTranscriptMetaRead):
    messages: list[dict[str, Any]] | None = None


class GroupChatTicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    activity_date: date
    chat_id: int
    club_id: int
    ticket_index: int
    start_msg_id: int
    end_msg_id: int
    message_ids: list[Any]
    brief_summary: str | None = None
    category: str
    events: dict[str, Any] | None = None
    summary: str | None = None
    prompt_version: str
    model: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    club_name: str | None = None
    group_name: str | None = None
    customer_first_message: str | None = None
    duration_seconds: int | None = None
    duration_source: Literal["resolution", "message_span"] | None = None


class TicketMessageRead(BaseModel):
    id: int
    date: str | None = None
    sender_id: int | None = None
    sender_name: str | None = None
    username: str | None = None
    is_bot: bool = False
    text: str | None = None
    media_type: str | None = None
    media_filename: str | None = None
    role: Literal["customer", "admin", "bot"]


class GroupChatTicketMessagesRead(BaseModel):
    ticket_id: int
    activity_date: date
    chat_id: int
    ticket_index: int
    messages: list[TicketMessageRead]


def _parse_activity_date(raw: str) -> date:
    text = (raw or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid activity_date: {raw!r}") from exc


def _ticket_to_read(
    row: GroupChatTicket,
    *,
    club_name: str | None,
    group_name: str | None,
    messages_by_id: dict[int, dict[str, Any]] | None,
) -> GroupChatTicketRead:
    events = row.events if isinstance(row.events, dict) else None
    duration_seconds, duration_source = compute_ticket_duration(
        events,
        row.message_ids if isinstance(row.message_ids, list) else None,
        messages_by_id,
    )
    return GroupChatTicketRead(
        id=row.id,
        activity_date=row.activity_date,
        chat_id=row.chat_id,
        club_id=row.club_id,
        ticket_index=row.ticket_index,
        start_msg_id=row.start_msg_id,
        end_msg_id=row.end_msg_id,
        message_ids=row.message_ids if isinstance(row.message_ids, list) else [],
        brief_summary=row.brief_summary,
        category=row.category,
        events=events,
        summary=row.summary,
        prompt_version=row.prompt_version,
        model=row.model,
        created_at=row.created_at,
        updated_at=row.updated_at,
        club_name=club_name,
        group_name=group_name or f"chat {row.chat_id}",
        customer_first_message=customer_first_from_events(events),
        duration_seconds=duration_seconds,
        duration_source=duration_source,
    )


def _enrich_tickets(db: Session, rows: list[GroupChatTicket]) -> list[GroupChatTicketRead]:
    if not rows:
        return []

    club_ids = {int(r.club_id) for r in rows}
    chat_ids = {int(r.chat_id) for r in rows}
    day = rows[0].activity_date

    clubs = {
        int(c.id): str(c.name)
        for c in db.query(Club).filter(Club.id.in_(club_ids)).all()
    }
    groups = {
        int(g.chat_id): (str(g.name).strip() if g.name else None)
        for g in db.query(Group).filter(Group.chat_id.in_(chat_ids)).all()
    }

    transcripts = (
        db.query(GroupChatDailyTranscript)
        .filter(
            GroupChatDailyTranscript.activity_date == day,
            GroupChatDailyTranscript.chat_id.in_(chat_ids),
        )
        .all()
    )
    messages_by_chat: dict[int, dict[int, dict[str, Any]]] = {
        int(t.chat_id): index_messages_by_id(t.messages) for t in transcripts
    }

    enriched = [
        _ticket_to_read(
            r,
            club_name=clubs.get(int(r.club_id)),
            group_name=groups.get(int(r.chat_id)),
            messages_by_id=messages_by_chat.get(int(r.chat_id)),
        )
        for r in rows
    ]
    enriched.sort(
        key=lambda t: (
            t.customer_first_message is None,
            t.customer_first_message or "",
            t.chat_id,
            t.ticket_index,
        )
    )
    return enriched


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


@router.get(
    "/api/group-chat-tickets",
    response_model=List[GroupChatTicketRead],
)
def list_group_chat_tickets(
    activity_date: str = Query(..., description="YYYY-MM-DD (America/New_York day)"),
    club_id: Optional[int] = Query(None),
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db_dependency),
):
    day = _parse_activity_date(activity_date)
    q = db.query(GroupChatTicket).filter(GroupChatTicket.activity_date == day)
    if club_id is not None:
        q = q.filter(GroupChatTicket.club_id == int(club_id))
    if category is not None:
        cat = category.strip().lower()
        q = q.filter(GroupChatTicket.category == cat)
    rows = q.order_by(
        GroupChatTicket.club_id,
        GroupChatTicket.chat_id,
        GroupChatTicket.ticket_index,
    ).all()
    return _enrich_tickets(db, rows)


@router.get(
    "/api/group-chat-tickets/by-id/{ticket_id}/messages",
    response_model=GroupChatTicketMessagesRead,
)
def get_group_chat_ticket_messages(
    ticket_id: int,
    db: Session = Depends(get_db_dependency),
):
    ticket = (
        db.query(GroupChatTicket)
        .filter(GroupChatTicket.id == int(ticket_id))
        .one_or_none()
    )
    if ticket is None:
        raise HTTPException(404, "Ticket not found")

    transcript = (
        db.query(GroupChatDailyTranscript)
        .filter(
            GroupChatDailyTranscript.activity_date == ticket.activity_date,
            GroupChatDailyTranscript.chat_id == int(ticket.chat_id),
        )
        .one_or_none()
    )
    if transcript is None:
        raise HTTPException(404, "Transcript not found")

    group = (
        db.query(Group)
        .filter(Group.chat_id == int(ticket.chat_id))
        .one_or_none()
    )
    group_name = (
        str(group.name).strip()
        if group is not None and (group.name or "").strip()
        else None
    )

    message_ids = ticket.message_ids if isinstance(ticket.message_ids, list) else []
    sliced = slice_ticket_messages(
        messages=transcript.messages if isinstance(transcript.messages, list) else [],
        message_ids=message_ids,
        club_id=int(ticket.club_id),
        group_name=group_name,
    )
    return GroupChatTicketMessagesRead(
        ticket_id=int(ticket.id),
        activity_date=ticket.activity_date,
        chat_id=int(ticket.chat_id),
        ticket_index=int(ticket.ticket_index),
        messages=[TicketMessageRead.model_validate(m) for m in sliced],
    )


@router.get(
    "/api/group-chat-tickets/{chat_id}",
    response_model=List[GroupChatTicketRead],
)
def list_group_chat_tickets_for_chat(
    chat_id: int,
    activity_date: str = Query(..., description="YYYY-MM-DD (America/New_York day)"),
    db: Session = Depends(get_db_dependency),
):
    day = _parse_activity_date(activity_date)
    rows = (
        db.query(GroupChatTicket)
        .filter(
            GroupChatTicket.activity_date == day,
            GroupChatTicket.chat_id == int(chat_id),
        )
        .order_by(GroupChatTicket.ticket_index)
        .all()
    )
    return _enrich_tickets(db, rows)
