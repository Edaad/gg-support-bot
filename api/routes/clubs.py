from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.schemas import ClubCreate, ClubUpdate, ClubRead, GroupRead
from db.connection import get_db_dependency
from db.models import Club

router = APIRouter(prefix="/api/clubs", tags=["clubs"], dependencies=[Depends(get_current_admin)])


def _club_to_read(club: Club) -> ClubRead:
    return ClubRead(
        id=club.id,
        name=club.name,
        telegram_user_id=club.telegram_user_id,
        welcome_type=club.welcome_type,
        welcome_text=club.welcome_text,
        welcome_file_id=club.welcome_file_id,
        welcome_caption=club.welcome_caption,
        list_type=club.list_type,
        list_text=club.list_text,
        list_file_id=club.list_file_id,
        list_caption=club.list_caption,
        allow_multi_cashout=club.allow_multi_cashout,
        allow_admin_commands=club.allow_admin_commands,
        deposit_simple_mode=club.deposit_simple_mode or False,
        deposit_simple_type=club.deposit_simple_type,
        deposit_simple_text=club.deposit_simple_text,
        deposit_simple_file_id=club.deposit_simple_file_id,
        deposit_simple_caption=club.deposit_simple_caption,
        cashout_simple_mode=club.cashout_simple_mode or False,
        cashout_simple_type=club.cashout_simple_type,
        cashout_simple_text=club.cashout_simple_text,
        cashout_simple_file_id=club.cashout_simple_file_id,
        cashout_simple_caption=club.cashout_simple_caption,
        is_active=club.is_active,
        created_at=club.created_at,
        method_count=len(club.payment_methods),
        group_count=len(club.groups),
    )


@router.get("", response_model=List[ClubRead])
def list_clubs(db: Session = Depends(get_db_dependency)):
    clubs = db.query(Club).order_by(Club.id).all()
    return [_club_to_read(c) for c in clubs]


@router.post("", response_model=ClubRead, status_code=201)
def create_club(body: ClubCreate, db: Session = Depends(get_db_dependency)):
    existing = db.query(Club).filter_by(telegram_user_id=body.telegram_user_id).first()
    if existing:
        raise HTTPException(409, "A club with that Telegram user ID already exists")
    club = Club(**body.model_dump())
    db.add(club)
    db.flush()
    db.refresh(club)
    return _club_to_read(club)


@router.get("/{club_id}", response_model=ClubRead)
def get_club(club_id: int, db: Session = Depends(get_db_dependency)):
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    return _club_to_read(club)


@router.put("/{club_id}", response_model=ClubRead)
def update_club(club_id: int, body: ClubUpdate, db: Session = Depends(get_db_dependency)):
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(club, field, value)
    db.flush()
    db.refresh(club)
    return _club_to_read(club)


@router.delete("/{club_id}", status_code=204)
def delete_club(club_id: int, db: Session = Depends(get_db_dependency)):
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    db.delete(club)


@router.get("/{club_id}/groups", response_model=List[GroupRead])
def list_club_groups(club_id: int, db: Session = Depends(get_db_dependency)):
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    return [GroupRead.model_validate(g) for g in club.groups]
