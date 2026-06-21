"""CRUD for staff cashout records and payment lines."""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.schemas import (
    StaffCashoutPaymentCreate,
    StaffCashoutPaymentRead,
    StaffCashoutPaymentUpdate,
    StaffCashoutRecordRead,
    StaffCashoutRecordUpdate,
)
from bot.services.staff_cashout_records import (
    add_staff_cashout_payment,
    delete_staff_cashout_payment,
    get_staff_cashout_record,
    list_staff_cashout_records,
    replace_staff_cashout_payments,
    update_staff_cashout_payment,
    update_staff_cashout_record,
)
from cashier.services.zapier import fire_zapier_webhook_for_record
from db.connection import get_db_dependency
from db.models import Club

router = APIRouter(
    prefix="/api/cashout-records",
    tags=["cashout-records"],
    dependencies=[Depends(get_current_admin)],
)


def _club_name_map(db: Session) -> dict[int, str]:
    return {int(row.id): str(row.name) for row in db.query(Club.id, Club.name).all()}


def _to_read(data: dict, club_names: dict[int, str]) -> StaffCashoutRecordRead:
    club_id = int(data["club_id"])
    return StaffCashoutRecordRead(
        id=data["id"],
        cashier_job_id=data["cashier_job_id"],
        club_id=club_id,
        club_name=club_names.get(club_id),
        chat_id=data["chat_id"],
        group_title=data["group_title"],
        gg_player_id=data.get("gg_player_id"),
        amount=data["amount"],
        recorded_by_telegram_user_id=data["recorded_by_telegram_user_id"],
        trigger=data["trigger"],
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        payments=[StaffCashoutPaymentRead.model_validate(p) for p in data.get("payments", [])],
    )


async def _sync_or_502(record_id: int) -> None:
    ok, err = await fire_zapier_webhook_for_record(record_id)
    if not ok:
        raise HTTPException(502, err or "Zapier sync failed")


@router.get("", response_model=List[StaffCashoutRecordRead])
def list_cashout_records(
    club_id: Optional[int] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db_dependency),
):
    club_names = _club_name_map(db)
    rows = list_staff_cashout_records(club_id=club_id, limit=limit)
    return [_to_read(row, club_names) for row in rows]


@router.get("/{record_id}", response_model=StaffCashoutRecordRead)
def get_cashout_record(
    record_id: int,
    db: Session = Depends(get_db_dependency),
):
    data = get_staff_cashout_record(record_id)
    if not data:
        raise HTTPException(404, "Cashout record not found")
    return _to_read(data, _club_name_map(db))


@router.patch("/{record_id}", response_model=StaffCashoutRecordRead)
async def patch_cashout_record(
    record_id: int,
    body: StaffCashoutRecordUpdate,
    db: Session = Depends(get_db_dependency),
):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        data = get_staff_cashout_record(record_id)
        if not data:
            raise HTTPException(404, "Cashout record not found")
        return _to_read(data, _club_name_map(db))

    data = update_staff_cashout_record(
        record_id,
        group_title=updates.get("group_title"),
        amount=updates.get("amount"),
    )
    if not data:
        raise HTTPException(404, "Cashout record not found")

    await _sync_or_502(record_id)
    return _to_read(data, _club_name_map(db))


@router.put("/{record_id}/payments", response_model=StaffCashoutRecordRead)
async def replace_payments(
    record_id: int,
    body: List[StaffCashoutPaymentCreate],
    db: Session = Depends(get_db_dependency),
):
    if not body:
        raise HTTPException(400, "At least one payment line is required")
    try:
        data = replace_staff_cashout_payments(
            record_id,
            [p.model_dump() for p in body],
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not data:
        raise HTTPException(404, "Cashout record not found")

    await _sync_or_502(record_id)
    return _to_read(data, _club_name_map(db))


@router.post("/{record_id}/payments", response_model=StaffCashoutRecordRead, status_code=201)
async def add_payment(
    record_id: int,
    body: StaffCashoutPaymentCreate,
    db: Session = Depends(get_db_dependency),
):
    data = add_staff_cashout_payment(record_id, body.model_dump())
    if not data:
        raise HTTPException(404, "Cashout record not found")

    await _sync_or_502(record_id)
    return _to_read(data, _club_name_map(db))


@router.patch("/{record_id}/payments/{payment_id}", response_model=StaffCashoutRecordRead)
async def patch_payment(
    record_id: int,
    payment_id: int,
    body: StaffCashoutPaymentUpdate,
    db: Session = Depends(get_db_dependency),
):
    updates = body.model_dump(exclude_unset=True)
    data = update_staff_cashout_payment(record_id, payment_id, updates)
    if not data:
        raise HTTPException(404, "Cashout record or payment not found")

    await _sync_or_502(record_id)
    return _to_read(data, _club_name_map(db))


@router.delete("/{record_id}/payments/{payment_id}", response_model=StaffCashoutRecordRead)
async def remove_payment(
    record_id: int,
    payment_id: int,
    db: Session = Depends(get_db_dependency),
):
    try:
        data = delete_staff_cashout_payment(record_id, payment_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not data:
        raise HTTPException(404, "Cashout record or payment not found")

    await _sync_or_502(record_id)
    return _to_read(data, _club_name_map(db))


@router.post("/{record_id}/sync")
async def sync_cashout_record(record_id: int):
    if not get_staff_cashout_record(record_id):
        raise HTTPException(404, "Cashout record not found")
    await _sync_or_502(record_id)
    return {"ok": True}
