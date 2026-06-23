"""Pending issue report drafts (group /escalate → DM wizard)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from db.models import IssueReportDraft

DRAFT_TTL_MINUTES = 30


@dataclass(frozen=True)
class DraftContext:
    id: int
    club_id: int | None
    group_title: str | None
    telegram_chat_id: int | None


def draft_to_context(draft: IssueReportDraft) -> DraftContext:
    """Copy draft fields while the SQLAlchemy session is still open."""
    return DraftContext(
        id=int(draft.id),
        club_id=int(draft.club_id) if draft.club_id is not None else None,
        group_title=draft.group_title,
        telegram_chat_id=draft.telegram_chat_id,
    )


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def create_draft(
    session: Session,
    *,
    staff_telegram_user_id: int,
    club_id: int | None = None,
    group_title: str | None = None,
    telegram_chat_id: int | None = None,
) -> IssueReportDraft:
    now = datetime.now(timezone.utc)
    draft = IssueReportDraft(
        staff_telegram_user_id=staff_telegram_user_id,
        club_id=club_id,
        group_title=(group_title or "").strip() or None,
        telegram_chat_id=telegram_chat_id,
        status="pending",
        expires_at=now + timedelta(minutes=DRAFT_TTL_MINUTES),
    )
    session.add(draft)
    session.flush()
    return draft


def get_pending_draft(
    session: Session, draft_id: int, *, staff_telegram_user_id: int
) -> IssueReportDraft | None:
    now = datetime.now(timezone.utc)
    draft = (
        session.query(IssueReportDraft)
        .filter(
            IssueReportDraft.id == draft_id,
            IssueReportDraft.staff_telegram_user_id == staff_telegram_user_id,
            IssueReportDraft.status == "pending",
        )
        .first()
    )
    if draft is None:
        return None
    if _as_utc(draft.expires_at) <= now:
        return None
    return draft


def get_latest_pending_draft(
    session: Session, *, staff_telegram_user_id: int
) -> IssueReportDraft | None:
    now = datetime.now(timezone.utc)
    drafts = (
        session.query(IssueReportDraft)
        .filter(
            IssueReportDraft.staff_telegram_user_id == staff_telegram_user_id,
            IssueReportDraft.status == "pending",
        )
        .order_by(IssueReportDraft.created_at.desc())
        .limit(5)
        .all()
    )
    for draft in drafts:
        if _as_utc(draft.expires_at) > now:
            return draft
    return None


def mark_draft_submitted(session: Session, draft_id: int) -> None:
    draft = session.query(IssueReportDraft).filter(IssueReportDraft.id == draft_id).first()
    if draft:
        draft.status = "submitted"


def cancel_draft(session: Session, draft_id: int) -> None:
    draft = session.query(IssueReportDraft).filter(IssueReportDraft.id == draft_id).first()
    if draft and draft.status == "pending":
        draft.status = "cancelled"
