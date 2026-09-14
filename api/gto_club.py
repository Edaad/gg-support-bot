"""ClubGTO scoping helpers for the dashboard GTO role."""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api.auth import ROLE_GTO
from config import CLUB_SHORTHAND_TO_NAME
from db.models import Club

GTO_CLUB_NAME = CLUB_SHORTHAND_TO_NAME["GTO"]  # "ClubGTO"


def lookup_gto_club_id(db: Session) -> Optional[int]:
    club = db.query(Club).filter(Club.name == GTO_CLUB_NAME).first()
    if club is None:
        return None
    return int(club.id)


def resolve_gto_list_club_id(
    role: str,
    club_id: Optional[int],
    db: Session,
) -> tuple[Optional[int], bool]:
    """Return (effective_club_id, empty_result).

    For non-GTO roles, returns (club_id, False).
    For GTO: forces ClubGTO; if missing, empty_result=True (lists should return []).
    If GTO passes a non-ClubGTO club_id, raises 403.
    """
    if role != ROLE_GTO:
        return club_id, False
    gto_id = lookup_gto_club_id(db)
    if gto_id is None:
        if club_id is not None:
            raise HTTPException(403, "Club access denied")
        return None, True
    if club_id is not None and int(club_id) != gto_id:
        raise HTTPException(403, "Club access denied")
    return gto_id, False


def require_gto_club_id_for_write(
    role: str,
    club_id: int,
    db: Session,
) -> int:
    """For creates: GTO must use ClubGTO (404 if missing, 403 if wrong club)."""
    if role != ROLE_GTO:
        return club_id
    gto_id = lookup_gto_club_id(db)
    if gto_id is None:
        raise HTTPException(404, "ClubGTO not found")
    if int(club_id) != gto_id:
        raise HTTPException(403, "Club access denied")
    return gto_id


def assert_gto_record_club(
    role: str,
    record_club_id: Optional[int],
    db: Session,
) -> None:
    """After loading a record by id: GTO may only touch ClubGTO rows."""
    if role != ROLE_GTO:
        return
    gto_id = lookup_gto_club_id(db)
    if gto_id is None or record_club_id is None or int(record_club_id) != gto_id:
        raise HTTPException(403, "Club access denied")


def assert_gto_club_id(role: str, club_id: int, db: Session) -> None:
    """Path/id access: GTO may only use ClubGTO."""
    assert_gto_record_club(role, club_id, db)
