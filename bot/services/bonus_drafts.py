"""Pending bonus drafts (group /add with bonus → DM wizard)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from db.models import BonusDraft

DRAFT_TTL_MINUTES = 30


@dataclass(frozen=True)
class BonusDraftContext:
    id: int
    club_id: int | None
    group_title: str | None
    telegram_chat_id: int | None
    gg_player_id: str | None
    player_details_id: int | None
    amount: Decimal


def draft_to_context(draft: BonusDraft) -> BonusDraftContext:
    """Copy draft fields while the SQLAlchemy session is still open."""
    return BonusDraftContext(
        id=int(draft.id),
        club_id=int(draft.club_id) if draft.club_id is not None else None,
        group_title=draft.group_title,
        telegram_chat_id=draft.telegram_chat_id,
        gg_player_id=draft.gg_player_id,
        player_details_id=(
            int(draft.player_details_id) if draft.player_details_id is not None else None
        ),
        amount=Decimal(str(draft.amount)),
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
    gg_player_id: str | None = None,
    player_details_id: int | None = None,
    amount: Decimal,
) -> BonusDraft:
    now = datetime.now(timezone.utc)
    draft = BonusDraft(
        staff_telegram_user_id=staff_telegram_user_id,
        club_id=club_id,
        group_title=(group_title or "").strip() or None,
        telegram_chat_id=telegram_chat_id,
        gg_player_id=(gg_player_id or "").strip() or None,
        player_details_id=player_details_id,
        amount=amount,
        status="pending",
        expires_at=now + timedelta(minutes=DRAFT_TTL_MINUTES),
    )
    session.add(draft)
    session.flush()
    return draft


def get_pending_draft(
    session: Session, draft_id: int, *, staff_telegram_user_id: int
) -> BonusDraft | None:
    now = datetime.now(timezone.utc)
    draft = (
        session.query(BonusDraft)
        .filter(
            BonusDraft.id == draft_id,
            BonusDraft.staff_telegram_user_id == staff_telegram_user_id,
            BonusDraft.status == "pending",
        )
        .first()
    )
    if draft is None:
        return None
    if _as_utc(draft.expires_at) <= now:
        return None
    return draft


def mark_draft_submitted(session: Session, draft_id: int) -> None:
    draft = session.query(BonusDraft).filter(BonusDraft.id == draft_id).first()
    if draft:
        draft.status = "submitted"


def cancel_draft(session: Session, draft_id: int) -> None:
    draft = session.query(BonusDraft).filter(BonusDraft.id == draft_id).first()
    if draft and draft.status == "pending":
        draft.status = "cancelled"
