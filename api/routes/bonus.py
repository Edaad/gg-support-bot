"""CRUD for bonus types and read-only listing of bonus records."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.schemas import (
    BonusTypeCreate,
    BonusTypeUpdate,
    BonusTypeRead,
    BonusRecordRead,
)
from db.connection import get_db_dependency
from db.models import BonusType, BonusRecord

router = APIRouter(
    prefix="/api/bonus",
    tags=["bonus"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("/types", response_model=List[BonusTypeRead])
def list_bonus_types(db: Session = Depends(get_db_dependency)):
    return [
        BonusTypeRead.model_validate(bt)
        for bt in db.query(BonusType).order_by(BonusType.sort_order, BonusType.id).all()
    ]


@router.post("/types", response_model=BonusTypeRead, status_code=201)
def create_bonus_type(body: BonusTypeCreate, db: Session = Depends(get_db_dependency)):
    bt = BonusType(**body.model_dump())
    db.add(bt)
    db.flush()
    db.refresh(bt)
    return BonusTypeRead.model_validate(bt)


@router.put("/types/{type_id}", response_model=BonusTypeRead)
def update_bonus_type(
    type_id: int, body: BonusTypeUpdate, db: Session = Depends(get_db_dependency)
):
    bt = db.query(BonusType).get(type_id)
    if not bt:
        raise HTTPException(404, "Bonus type not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(bt, field, value)
    db.flush()
    db.refresh(bt)
    return BonusTypeRead.model_validate(bt)


@router.delete("/types/{type_id}", status_code=204)
def delete_bonus_type(type_id: int, db: Session = Depends(get_db_dependency)):
    bt = db.query(BonusType).get(type_id)
    if not bt:
        raise HTTPException(404, "Bonus type not found")
    db.delete(bt)


@router.get("/records", response_model=List[BonusRecordRead])
def list_bonus_records(db: Session = Depends(get_db_dependency)):
    rows = (
        db.query(BonusRecord)
        .order_by(BonusRecord.created_at.desc())
        .limit(200)
        .all()
    )
    results = []
    for r in rows:
        results.append(
            BonusRecordRead(
                id=r.id,
                player_username=r.player_username,
                amount=r.amount,
                bonus_type_name=r.bonus_type.name if r.bonus_type else None,
                custom_description=r.custom_description,
                club_name=r.club.name if r.club else None,
                admin_telegram_user_id=r.admin_telegram_user_id,
                created_at=r.created_at,
            )
        )
    return results
