"""CRUD for bonus types and bonus records."""

from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from db.connection import get_db_dependency

from api.auth import ROLE_GTO, get_current_admin
from api.gto_club import (
    assert_gto_record_club,
    require_gto_club_id_for_write,
    resolve_gto_list_club_id,
)
from api.record_csv_export import (
    build_bonus_records_csv,
    csv_streaming_response,
    et_range_to_utc_naive,
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
from db.models import BonusRecord, BonusType

router = APIRouter(
    prefix="/api/bonus",
    tags=["bonus"],
    dependencies=[Depends(get_current_admin)],
)


def _to_read(data: dict) -> BonusRecordRead:
    return BonusRecordRead.model_validate(data)


def _parse_et_day(raw: Optional[str]) -> Optional[date]:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise HTTPException(400, f"Invalid date: {raw!r}") from exc


def _issued_bounds(
    from_raw: Optional[str], to_raw: Optional[str]
) -> tuple[Optional[datetime], Optional[datetime]]:
    """Inclusive America/New_York calendar days as naive UTC bounds."""

    from_day = _parse_et_day(from_raw)
    to_day = _parse_et_day(to_raw)
    if from_day and to_day and from_day > to_day:
        raise HTTPException(400, "from must be on or before to")
    issued_from = et_range_to_utc_naive(from_day, from_day)[0] if from_day else None
    issued_to = et_range_to_utc_naive(to_day, to_day)[1] if to_day else None
    return issued_from, issued_to


def _load_bonus_and_assert_gto(record_id: int, role: str, db: Session) -> None:
    if role != ROLE_GTO:
        return
    record = db.query(BonusRecord).get(record_id)
    if not record:
        raise HTTPException(404, "Bonus record not found")
    assert_gto_record_club(role, record.club_id, db)


@router.get("/types", response_model=List[BonusTypeRead])
def list_bonus_types(db: Session = Depends(get_db_dependency)):
    return [
        BonusTypeRead.model_validate(bt)
        for bt in db.query(BonusType).order_by(BonusType.sort_order, BonusType.id).all()
    ]


@router.post("/types", response_model=BonusTypeRead, status_code=201)
def create_bonus_type(body: BonusTypeCreate, db: Session = Depends(get_db_dependency)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name is required")
    existing = db.query(BonusType).filter(BonusType.name.ilike(name)).first()
    if existing:
        raise HTTPException(409, f"Bonus type {existing.name!r} already exists")
    bt = BonusType(**{**body.model_dump(), "name": name})
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
    from_date: Optional[str] = Query(
        None, alias="from", description="YYYY-MM-DD (ET, inclusive)"
    ),
    to_date: Optional[str] = Query(
        None, alias="to", description="YYYY-MM-DD (ET, inclusive)"
    ),
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    effective_club_id, empty = resolve_gto_list_club_id(role, club_id, db)
    if empty:
        return []
    issued_from, issued_to = _issued_bounds(from_date, to_date)
    return [
        _to_read(row)
        for row in list_bonus_record_rows(
            club_id=effective_club_id,
            bonus_type_id=bonus_type_id,
            other=other,
            q=q,
            issued_from=issued_from,
            issued_to=issued_to,
        )
    ]


@router.get("/records/export")
def export_bonus_records_csv(
    from_date: str = Query(..., alias="from", description="YYYY-MM-DD (ET, inclusive)"),
    to_date: str = Query(..., alias="to", description="YYYY-MM-DD (ET, inclusive)"),
    club_id: Optional[int] = Query(None),
    bonus_type_id: Optional[int] = Query(None),
    other: bool = Query(False),
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    effective_club_id, empty = resolve_gto_list_club_id(role, club_id, db)
    try:
        from_day, to_day = parse_inclusive_date_range(from_date, to_date)
        if empty:
            content = build_bonus_records_csv(
                db,
                from_day=from_day,
                to_day=to_day,
                club_id=-1,
                bonus_type_id=bonus_type_id,
                other=other,
            )
        else:
            content = build_bonus_records_csv(
                db,
                from_day=from_day,
                to_day=to_day,
                club_id=effective_club_id,
                bonus_type_id=bonus_type_id,
                other=other,
            )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    filename = f"bonus-records-{from_day.isoformat()}-to-{to_day.isoformat()}.csv"
    return csv_streaming_response(content, filename)


@router.post("/records", response_model=BonusRecordRead, status_code=201)
def create_bonus_record_api(
    body: BonusRecordCreate,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    club_id = require_gto_club_id_for_write(role, body.club_id, db)
    try:
        data = create_bonus_record(
            club_id=club_id,
            group_title=body.group_title,
            amount=body.amount,
            bonus_type_id=body.bonus_type_id,
            custom_description=body.custom_description,
            issued_at=body.issued_at,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _to_read(data)


@router.patch("/records/{record_id}", response_model=BonusRecordRead)
def update_bonus_record_api(
    record_id: int,
    body: BonusRecordUpdate,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    _load_bonus_and_assert_gto(record_id, role, db)
    updates = body.model_dump(exclude_unset=True)
    if "club_id" in updates and updates["club_id"] is not None:
        updates["club_id"] = require_gto_club_id_for_write(
            role, int(updates["club_id"]), db
        )
    try:
        data = update_bonus_record(record_id, **updates)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not data:
        raise HTTPException(404, "Bonus record not found")
    return _to_read(data)


@router.delete("/records/{record_id}", status_code=204)
def delete_bonus_record_api(
    record_id: int,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    _load_bonus_and_assert_gto(record_id, role, db)
    if not delete_bonus_record(record_id):
        raise HTTPException(404, "Bonus record not found")
