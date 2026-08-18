"""CRUD for bonus types and bonus records."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from db.connection import get_db_dependency

from api.auth import get_current_admin
from api.record_csv_export import (
    build_bonus_records_csv,
    csv_streaming_response,
    parse_inclusive_date_range,
)
from api.schemas import (
    BonusRecordCreate,
    BonusRecordRead,
    BonusRecordUpdate,
    BonusTypeCreate,
    BonusTypeRead,
    BonusTypeUpdate,
)
from bot.services.bonus_records import (
    create_bonus_record,
    delete_bonus_record,
    list_bonus_records as list_bonus_record_rows,
    update_bonus_record,
)
from db.models import BonusType

router = APIRouter(
    prefix="/api/bonus",
    tags=["bonus"],
    dependencies=[Depends(get_current_admin)],
)


def _to_read(data: dict) -> BonusRecordRead:
    return BonusRecordRead.model_validate(data)


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
def list_bonus_records(
    club_id: Optional[int] = Query(None),
    bonus_type_id: Optional[int] = Query(None),
    other: bool = Query(False),
    q: Optional[str] = Query(None),
):
    return [
        _to_read(row)
        for row in list_bonus_record_rows(
            club_id=club_id,
            bonus_type_id=bonus_type_id,
            other=other,
            q=q,
        )
    ]


@router.get("/records/export")
def export_bonus_records_csv(
    from_date: str = Query(..., alias="from", description="YYYY-MM-DD (ET, inclusive)"),
    to_date: str = Query(..., alias="to", description="YYYY-MM-DD (ET, inclusive)"),
    club_id: Optional[int] = Query(None),
    bonus_type_id: Optional[int] = Query(None),
    other: bool = Query(False),
    db: Session = Depends(get_db_dependency),
):
    try:
        from_day, to_day = parse_inclusive_date_range(from_date, to_date)
        content = build_bonus_records_csv(
            db,
            from_day=from_day,
            to_day=to_day,
            club_id=club_id,
            bonus_type_id=bonus_type_id,
            other=other,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    filename = f"bonus-records-{from_day.isoformat()}-to-{to_day.isoformat()}.csv"
    return csv_streaming_response(content, filename)


@router.post("/records", response_model=BonusRecordRead, status_code=201)
def create_bonus_record_api(body: BonusRecordCreate):
    try:
        data = create_bonus_record(
            club_id=body.club_id,
            group_title=body.group_title,
            amount=body.amount,
            bonus_type_id=body.bonus_type_id,
            custom_description=body.custom_description,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _to_read(data)


@router.patch("/records/{record_id}", response_model=BonusRecordRead)
def update_bonus_record_api(record_id: int, body: BonusRecordUpdate):
    try:
        data = update_bonus_record(record_id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not data:
        raise HTTPException(404, "Bonus record not found")
    return _to_read(data)


@router.delete("/records/{record_id}", status_code=204)
def delete_bonus_record_api(record_id: int):
    if not delete_bonus_record(record_id):
        raise HTTPException(404, "Bonus record not found")
