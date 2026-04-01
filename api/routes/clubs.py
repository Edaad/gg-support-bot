from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.schemas import (
    ClubCreate,
    ClubUpdate,
    ClubRead,
    GroupRead,
    LinkedAccountCreate,
    LinkedAccountRead,
)
from db.connection import get_db_dependency
from db.models import Club, ClubLinkedAccount

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
        cashout_cooldown_enabled=club.cashout_cooldown_enabled or False,
        cashout_cooldown_hours=club.cashout_cooldown_hours or 24,
        cashout_hours_enabled=club.cashout_hours_enabled or False,
        cashout_hours_start=club.cashout_hours_start,
        cashout_hours_end=club.cashout_hours_end,
        is_active=club.is_active,
        created_at=club.created_at,
        method_count=len(club.payment_methods),
        group_count=len(club.groups),
        linked_account_count=len(club.linked_accounts),
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
    if db.query(ClubLinkedAccount).filter_by(telegram_user_id=body.telegram_user_id).first():
        raise HTTPException(
            409, "That Telegram user ID is already linked to a club as a backup account"
        )
    club = Club(**body.model_dump())
    db.add(club)
    db.flush()
    db.refresh(club)
    return _club_to_read(club)


@router.get("/{club_id}/linked-accounts", response_model=List[LinkedAccountRead])
def list_linked_accounts(club_id: int, db: Session = Depends(get_db_dependency)):
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    return [LinkedAccountRead.model_validate(a) for a in club.linked_accounts]


@router.post("/{club_id}/linked-accounts", response_model=LinkedAccountRead, status_code=201)
def add_linked_account(
    club_id: int, body: LinkedAccountCreate, db: Session = Depends(get_db_dependency)
):
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    tid = body.telegram_user_id
    if tid == club.telegram_user_id:
        raise HTTPException(
            400, "This Telegram user ID is already the primary account for this club"
        )
    if db.query(Club).filter_by(telegram_user_id=tid).first():
        raise HTTPException(
            409, "That Telegram user ID is already a primary account for another club"
        )
    if db.query(ClubLinkedAccount).filter_by(telegram_user_id=tid).first():
        raise HTTPException(409, "That Telegram user ID is already linked to a club")
    row = ClubLinkedAccount(club_id=club_id, telegram_user_id=tid)
    db.add(row)
    db.flush()
    db.refresh(row)
    return LinkedAccountRead.model_validate(row)


@router.delete("/{club_id}/linked-accounts/{account_id}", status_code=204)
def delete_linked_account(
    club_id: int, account_id: int, db: Session = Depends(get_db_dependency)
):
    row = db.query(ClubLinkedAccount).filter_by(id=account_id, club_id=club_id).first()
    if not row:
        raise HTTPException(404, "Linked account not found")
    db.delete(row)


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
    data = body.model_dump(exclude_unset=True)
    if "telegram_user_id" in data:
        new_tid = data["telegram_user_id"]
        if new_tid != club.telegram_user_id:
            other = db.query(Club).filter_by(telegram_user_id=new_tid).first()
            if other and other.id != club_id:
                raise HTTPException(
                    409, "Another club already uses this Telegram user ID as primary"
                )
            link = db.query(ClubLinkedAccount).filter_by(telegram_user_id=new_tid).first()
            if link:
                if link.club_id != club_id:
                    raise HTTPException(
                        409, "That Telegram user ID is linked to another club"
                    )
                db.delete(link)
    for field, value in data.items():
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
