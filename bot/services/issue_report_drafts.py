"""Pending issue report drafts (group /report → DM wizard)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from db.models import IssueReportDraft

DRAFT_TTL_MINUTES = 30


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
    return (
        session.query(IssueReportDraft)
        .filter(
            IssueReportDraft.id == draft_id,
            IssueReportDraft.staff_telegram_user_id == staff_telegram_user_id,
            IssueReportDraft.status == "pending",
            IssueReportDraft.expires_at > now,
        )
        .first()
    )


def get_latest_pending_draft(
    session: Session, *, staff_telegram_user_id: int
) -> IssueReportDraft | None:
    now = datetime.now(timezone.utc)
    return (
        session.query(IssueReportDraft)
        .filter(
            IssueReportDraft.staff_telegram_user_id == staff_telegram_user_id,
            IssueReportDraft.status == "pending",
            IssueReportDraft.expires_at > now,
        )
        .order_by(IssueReportDraft.created_at.desc())
        .first()
    )


def mark_draft_submitted(session: Session, draft_id: int) -> None:
    draft = session.query(IssueReportDraft).filter(IssueReportDraft.id == draft_id).first()
    if draft:
        draft.status = "submitted"


def cancel_draft(session: Session, draft_id: int) -> None:
    draft = session.query(IssueReportDraft).filter(IssueReportDraft.id == draft_id).first()
    if draft and draft.status == "pending":
        draft.status = "cancelled"
