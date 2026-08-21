"""CRUD for staff cashout records, destinations, and money sends."""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.auth import ROLE_ADMIN, get_current_admin, require_admin
from api.record_csv_export import (
    build_cashout_money_sends_csv,
    build_cashout_records_csv,
    csv_streaming_response,
    et_range_to_utc_naive,
    parse_inclusive_date_range,
)
from api.schemas import (
    StaffCashoutMoneySendLedgerRead,
    StaffCashoutPaymentCreate,
    StaffCashoutPaymentRead,
    StaffCashoutPaymentUpdate,
    StaffCashoutRecordCreate,
    StaffCashoutRecordRead,
    StaffCashoutRecordUpdate,
    StaffCashoutSendCreate,
    StaffCashoutSendRead,
    StaffCashoutSendUpdate,
)
from bot.services.staff_cashout_records import (
    CashoutRecordNotActive,
    add_staff_cashout_payment,
    add_staff_cashout_send,
    create_staff_cashout_record_manual,
    delete_staff_cashout_payment,
    delete_staff_cashout_send,
    get_staff_cashout_record,
    list_money_send_method_names,
    list_staff_cashout_money_sends,
    list_staff_cashout_records,
    replace_staff_cashout_payments,
    update_staff_cashout_payment,
    update_staff_cashout_record,
    update_staff_cashout_send,
)
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
    sent = data.get("sent", Decimal("0"))
    remaining = data.get("remaining", Decimal("0"))
    return StaffCashoutRecordRead(
        id=data["id"],
        cashier_job_id=data.get("cashier_job_id"),
        club_id=club_id,
        club_name=club_names.get(club_id),
        chat_id=data.get("chat_id"),
        group_title=data["group_title"],
        gg_player_id=data.get("gg_player_id"),
        amount=data["amount"],
        recorded_by_telegram_user_id=data.get("recorded_by_telegram_user_id"),
        trigger=data["trigger"],
        tracks_money_sent=bool(data.get("tracks_money_sent")),
        do_not_send=bool(data.get("do_not_send")),
        sent=sent,
        remaining=remaining,
        status=str(data.get("status") or "cleared"),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        payments=[StaffCashoutPaymentRead.model_validate(p) for p in data.get("payments", [])],
        sends=[StaffCashoutSendRead.model_validate(s) for s in data.get("sends", [])],
    )


@router.get("", response_model=List[StaffCashoutRecordRead])
def list_cashout_records(
    club_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    if status == "do_not_send" and role != ROLE_ADMIN:
        raise HTTPException(403, "Admin only")
    club_names = _club_name_map(db)
    try:
        rows = list_staff_cashout_records(club_id=club_id, status=status, q=q, limit=limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return [_to_read(row, club_names) for row in rows]


@router.get("/export")
def export_cashout_records_csv(
    from_date: str = Query(..., alias="from", description="YYYY-MM-DD (ET, inclusive)"),
    to_date: str = Query(..., alias="to", description="YYYY-MM-DD (ET, inclusive)"),
    club_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db_dependency),
):
    try:
        from_day, to_day = parse_inclusive_date_range(from_date, to_date)
        content = build_cashout_records_csv(
            db,
            from_day=from_day,
            to_day=to_day,
            club_id=club_id,
            status=status,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    filename = f"cashout-records-{from_day.isoformat()}-to-{to_day.isoformat()}.csv"
    return csv_streaming_response(content, filename)


def _ledger_to_read(data: dict) -> StaffCashoutMoneySendLedgerRead:
    return StaffCashoutMoneySendLedgerRead.model_validate(data)


@router.get("/sends", response_model=List[StaffCashoutMoneySendLedgerRead])
def list_cashout_money_sends(
    _admin: str = Depends(require_admin),
    club_id: Optional[int] = Query(None),
    from_date: str = Query(..., alias="from", description="YYYY-MM-DD (ET, inclusive)"),
    to_date: str = Query(..., alias="to", description="YYYY-MM-DD (ET, inclusive)"),
    method: Optional[str] = Query(None, description="Exact method_display_name"),
    q: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=500),
):
    try:
        from_day, to_day = parse_inclusive_date_range(from_date, to_date)
        start, end = et_range_to_utc_naive(from_day, to_day)
        rows = list_staff_cashout_money_sends(
            club_id=club_id,
            from_dt=start,
            to_dt=end,
            method_display_name=method,
            q=q,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return [_ledger_to_read(row) for row in rows]


@router.get("/sends/methods", response_model=List[str])
def list_cashout_money_send_methods(
    _admin: str = Depends(require_admin),
    club_id: Optional[int] = Query(None),
    from_date: str = Query(..., alias="from", description="YYYY-MM-DD (ET, inclusive)"),
    to_date: str = Query(..., alias="to", description="YYYY-MM-DD (ET, inclusive)"),
):
    try:
        from_day, to_day = parse_inclusive_date_range(from_date, to_date)
        start, end = et_range_to_utc_naive(from_day, to_day)
        return list_money_send_method_names(
            club_id=club_id,
            from_dt=start,
            to_dt=end,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/sends/export")
def export_cashout_money_sends_csv(
    _admin: str = Depends(require_admin),
    from_date: str = Query(..., alias="from", description="YYYY-MM-DD (ET, inclusive)"),
    to_date: str = Query(..., alias="to", description="YYYY-MM-DD (ET, inclusive)"),
    club_id: Optional[int] = Query(None),
    method: Optional[str] = Query(None, description="Exact method_display_name"),
    q: Optional[str] = Query(None),
):
    try:
        from_day, to_day = parse_inclusive_date_range(from_date, to_date)
        content = build_cashout_money_sends_csv(
            from_day=from_day,
            to_day=to_day,
            club_id=club_id,
            method_display_name=method,
            q=q,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    filename = f"cashout-money-sends-{from_day.isoformat()}-to-{to_day.isoformat()}.csv"
    return csv_streaming_response(content, filename)


@router.post("", response_model=StaffCashoutRecordRead, status_code=201)
def create_cashout_record(
    body: StaffCashoutRecordCreate,
    db: Session = Depends(get_db_dependency),
):
    try:
        data = create_staff_cashout_record_manual(
            club_id=body.club_id,
            group_title=body.group_title,
            amount=body.amount,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _to_read(data, _club_name_map(db))


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
def patch_cashout_record(
    record_id: int,
    body: StaffCashoutRecordUpdate,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        data = get_staff_cashout_record(record_id)
        if not data:
            raise HTTPException(404, "Cashout record not found")
        return _to_read(data, _club_name_map(db))

    if "do_not_send" in updates and role != ROLE_ADMIN:
        raise HTTPException(403, "Admin only")

    try:
        data = update_staff_cashout_record(
            record_id,
            group_title=updates.get("group_title"),
            amount=updates.get("amount"),
            do_not_send=updates.get("do_not_send") if "do_not_send" in updates else None,
        )
    except CashoutRecordNotActive as exc:
        raise HTTPException(409, str(exc)) from exc
    if not data:
        raise HTTPException(404, "Cashout record not found")
    return _to_read(data, _club_name_map(db))


@router.put("/{record_id}/payments", response_model=StaffCashoutRecordRead)
def replace_payments(
    record_id: int,
    body: List[StaffCashoutPaymentCreate],
    db: Session = Depends(get_db_dependency),
):
    try:
        data = replace_staff_cashout_payments(
            record_id,
            [p.model_dump() for p in body],
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not data:
        raise HTTPException(404, "Cashout record not found")
    return _to_read(data, _club_name_map(db))


@router.post("/{record_id}/payments", response_model=StaffCashoutRecordRead, status_code=201)
def add_payment(
    record_id: int,
    body: StaffCashoutPaymentCreate,
    db: Session = Depends(get_db_dependency),
):
    try:
        data = add_staff_cashout_payment(record_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not data:
        raise HTTPException(404, "Cashout record not found")
    return _to_read(data, _club_name_map(db))


@router.patch("/{record_id}/payments/{payment_id}", response_model=StaffCashoutRecordRead)
def patch_payment(
    record_id: int,
    payment_id: int,
    body: StaffCashoutPaymentUpdate,
    db: Session = Depends(get_db_dependency),
):
    updates = body.model_dump(exclude_unset=True)
    try:
        data = update_staff_cashout_payment(record_id, payment_id, updates)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not data:
        raise HTTPException(404, "Cashout record or payment not found")
    return _to_read(data, _club_name_map(db))


@router.delete("/{record_id}/payments/{payment_id}", response_model=StaffCashoutRecordRead)
def remove_payment(
    record_id: int,
    payment_id: int,
    db: Session = Depends(get_db_dependency),
):
    data = delete_staff_cashout_payment(record_id, payment_id)
    if not data:
        raise HTTPException(404, "Cashout record or payment not found")
    return _to_read(data, _club_name_map(db))


@router.post("/{record_id}/sends", response_model=StaffCashoutRecordRead, status_code=201)
def add_send(
    record_id: int,
    body: StaffCashoutSendCreate,
    db: Session = Depends(get_db_dependency),
):
    try:
        data = add_staff_cashout_send(record_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not data:
        raise HTTPException(404, "Cashout record not found")
    return _to_read(data, _club_name_map(db))


@router.patch("/{record_id}/sends/{send_id}", response_model=StaffCashoutRecordRead)
def patch_send(
    record_id: int,
    send_id: int,
    body: StaffCashoutSendUpdate,
    db: Session = Depends(get_db_dependency),
):
    try:
        data = update_staff_cashout_send(
            record_id, send_id, body.model_dump(exclude_unset=True)
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not data:
        raise HTTPException(404, "Cashout record or send not found")
    return _to_read(data, _club_name_map(db))


@router.delete("/{record_id}/sends/{send_id}", response_model=StaffCashoutRecordRead)
def remove_send(
    record_id: int,
    send_id: int,
    db: Session = Depends(get_db_dependency),
):
    data = delete_staff_cashout_send(record_id, send_id)
    if not data:
        raise HTTPException(404, "Cashout record or send not found")
    return _to_read(data, _club_name_map(db))
