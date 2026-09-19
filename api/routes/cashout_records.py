"""CRUD for staff cashout records, destinations, and money sends."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.auth import ROLE_ADMIN, ROLE_GTO, get_current_admin, require_admin
from api.gto_club import (
    assert_gto_record_club,
    require_gto_club_id_for_write,
    resolve_gto_list_club_id,
)
from api.record_csv_export import (
    build_cashout_money_sends_csv,
    build_cashout_records_csv,
    csv_streaming_response,
    et_range_to_utc_naive,
    parse_inclusive_date_range,
)
from api.schemas import (
    StaffCashoutMoneySendLedgerRead,
    StaffCashoutMoneySendListResponse,
    StaffCashoutNotifyRecipientCreate,
    StaffCashoutNotifyRecipientRead,
    StaffCashoutNotifyRecipientsListResponse,
    StaffCashoutNotifyRecipientUpdate,
    StaffCashoutPaymentCreate,
    StaffCashoutPaymentRead,
    StaffCashoutPaymentUpdate,
    StaffCashoutRecordCreate,
    StaffCashoutRecordListResponse,
    StaffCashoutRecordRead,
    StaffCashoutRecordUpdate,
    StaffCashoutSendCreate,
    StaffCashoutSendRead,
    StaffCashoutSendUpdate,
    StaffCashoutSlackReminderRead,
    StaffCashoutSlackReminderUpdate,
)
from bot.services.staff_cashout_records import (
    CashoutRecordNotActive,
    add_staff_cashout_payment,
    add_staff_cashout_send,
    create_staff_cashout_record_manual,
    delete_staff_cashout_payment,
    delete_staff_cashout_record,
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

logger = logging.getLogger(__name__)

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
        sending=bool(data.get("sending")),
        do_not_send=bool(data.get("do_not_send")),
        audited=bool(data.get("audited")),
        sent=sent,
        remaining=remaining,
        status=str(data.get("status") or "cleared"),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        payments=[
            StaffCashoutPaymentRead.model_validate(p) for p in data.get("payments", [])
        ],
        sends=[StaffCashoutSendRead.model_validate(s) for s in data.get("sends", [])],
    )


def _load_and_assert_gto(record_id: int, role: str, db: Session) -> dict | None:
    """For GTO, load the record and enforce ClubGTO. Returns data when loaded."""
    if role != ROLE_GTO:
        return None
    data = get_staff_cashout_record(record_id)
    if not data:
        raise HTTPException(404, "Cashout record not found")
    assert_gto_record_club(role, data.get("club_id"), db)
    return data


@router.get("", response_model=StaffCashoutRecordListResponse)
def list_cashout_records(
    club_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    audited: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    if status == "do_not_send" and role != ROLE_ADMIN:
        raise HTTPException(403, "Admin only")
    if audited is not None and role != ROLE_ADMIN:
        raise HTTPException(403, "Admin only")
    effective_club_id, empty = resolve_gto_list_club_id(role, club_id, db)
    if empty:
        return StaffCashoutRecordListResponse(
            items=[], total=0, limit=limit, offset=offset
        )
    club_names = _club_name_map(db)
    try:
        rows, total = list_staff_cashout_records(
            club_id=effective_club_id,
            status=status,
            q=q,
            audited=audited,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return StaffCashoutRecordListResponse(
        items=[_to_read(row, club_names) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/export")
def export_cashout_records_csv(
    from_date: str = Query(..., alias="from", description="YYYY-MM-DD (ET, inclusive)"),
    to_date: str = Query(..., alias="to", description="YYYY-MM-DD (ET, inclusive)"),
    club_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    audited: Optional[bool] = Query(None),
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    if audited is not None and role != ROLE_ADMIN:
        raise HTTPException(403, "Admin only")
    effective_club_id, empty = resolve_gto_list_club_id(role, club_id, db)
    try:
        from_day, to_day = parse_inclusive_date_range(from_date, to_date)
        if empty:
            content = build_cashout_records_csv(
                db,
                from_day=from_day,
                to_day=to_day,
                club_id=-1,
                status=status,
                audited=audited,
            )
        else:
            content = build_cashout_records_csv(
                db,
                from_day=from_day,
                to_day=to_day,
                club_id=effective_club_id,
                status=status,
                audited=audited,
            )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    filename = f"cashout-records-{from_day.isoformat()}-to-{to_day.isoformat()}.csv"
    return csv_streaming_response(content, filename)


def _ledger_to_read(data: dict) -> StaffCashoutMoneySendLedgerRead:
    return StaffCashoutMoneySendLedgerRead.model_validate(data)


@router.get("/sends", response_model=StaffCashoutMoneySendListResponse)
def list_cashout_money_sends(
    _admin: str = Depends(require_admin),
    club_id: Optional[int] = Query(None),
    from_date: str = Query(..., alias="from", description="YYYY-MM-DD (ET, inclusive)"),
    to_date: str = Query(..., alias="to", description="YYYY-MM-DD (ET, inclusive)"),
    method: Optional[str] = Query(None, description="Exact method_display_name"),
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    try:
        from_day, to_day = parse_inclusive_date_range(from_date, to_date)
        start, end = et_range_to_utc_naive(from_day, to_day)
        rows, total = list_staff_cashout_money_sends(
            club_id=club_id,
            from_dt=start,
            to_dt=end,
            method_display_name=method,
            q=q,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return StaffCashoutMoneySendListResponse(
        items=[_ledger_to_read(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


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


@router.get("/slack-reminder", response_model=StaffCashoutSlackReminderRead)
def get_cashout_slack_reminder(
    _admin: str = Depends(require_admin),
):
    from bot.services.staff_cashout_slack_reminders import get_notify_control

    state = get_notify_control()
    return StaffCashoutSlackReminderRead(
        enabled=bool(state["enabled"]),
        hours_enabled=bool(state["hours_enabled"]),
        hours_start=str(state["hours_start"]),
        hours_end=str(state["hours_end"]),
    )


@router.patch("/slack-reminder", response_model=StaffCashoutSlackReminderRead)
async def patch_cashout_slack_reminder(
    body: StaffCashoutSlackReminderUpdate,
    _admin: str = Depends(require_admin),
):
    from bot.services.staff_cashout_slack_reminders import (
        cashout_staff_alerts_open,
        get_notify_control,
        send_due_cashout_reminders,
        set_notify_control,
    )

    updates = body.model_dump(exclude_unset=True)
    try:
        state = set_notify_control(**updates) if updates else get_notify_control()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if state["enabled"] and cashout_staff_alerts_open():
        try:
            sent = await send_due_cashout_reminders()
            if sent:
                logger.info("cashout_slack_reminder: immediate send count=%s", sent)
        except Exception:
            logger.exception("cashout_slack_reminder: immediate send failed")
    return StaffCashoutSlackReminderRead(
        enabled=bool(state["enabled"]),
        hours_enabled=bool(state["hours_enabled"]),
        hours_start=str(state["hours_start"]),
        hours_end=str(state["hours_end"]),
    )


def _recipient_read(data: dict) -> StaffCashoutNotifyRecipientRead:
    return StaffCashoutNotifyRecipientRead(
        id=int(data["id"]),
        name=str(data["name"]),
        pushover_user_key=str(data["pushover_user_key"]),
        methods=list(data.get("methods") or []),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
    )


@router.get(
    "/notify-recipients",
    response_model=StaffCashoutNotifyRecipientsListResponse,
)
def list_cashout_notify_recipients(
    _admin: str = Depends(require_admin),
):
    from bot.services.staff_cashout_pushover import (
        FIXED_RAILS,
        RAIL_LABELS,
        list_notify_recipients,
    )

    return StaffCashoutNotifyRecipientsListResponse(
        rails=[{"slug": s, "label": RAIL_LABELS[s]} for s in FIXED_RAILS],
        recipients=[_recipient_read(r) for r in list_notify_recipients()],
    )


@router.post(
    "/notify-recipients",
    response_model=StaffCashoutNotifyRecipientRead,
    status_code=201,
)
def create_cashout_notify_recipient(
    body: StaffCashoutNotifyRecipientCreate,
    _admin: str = Depends(require_admin),
):
    from bot.services.staff_cashout_pushover import create_notify_recipient

    try:
        data = create_notify_recipient(
            name=body.name,
            pushover_user_key=body.pushover_user_key,
            methods=list(body.methods or []),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _recipient_read(data)


@router.patch(
    "/notify-recipients/{recipient_id}",
    response_model=StaffCashoutNotifyRecipientRead,
)
def patch_cashout_notify_recipient(
    recipient_id: int,
    body: StaffCashoutNotifyRecipientUpdate,
    _admin: str = Depends(require_admin),
):
    from bot.services.staff_cashout_pushover import update_notify_recipient

    updates = body.model_dump(exclude_unset=True)
    try:
        data = update_notify_recipient(
            recipient_id,
            name=updates.get("name"),
            pushover_user_key=updates.get("pushover_user_key"),
            methods=updates.get("methods"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not data:
        raise HTTPException(404, "Recipient not found")
    return _recipient_read(data)


@router.delete("/notify-recipients/{recipient_id}", status_code=204)
def delete_cashout_notify_recipient(
    recipient_id: int,
    _admin: str = Depends(require_admin),
):
    from bot.services.staff_cashout_pushover import delete_notify_recipient

    if not delete_notify_recipient(recipient_id):
        raise HTTPException(404, "Recipient not found")
    return None


@router.post("", response_model=StaffCashoutRecordRead, status_code=201)
def create_cashout_record(
    body: StaffCashoutRecordCreate,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    club_id = require_gto_club_id_for_write(role, body.club_id, db)
    try:
        data = create_staff_cashout_record_manual(
            club_id=club_id,
            group_title=body.group_title,
            amount=body.amount,
            payments=[p.model_dump() for p in body.payments],
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _to_read(data, _club_name_map(db))


@router.get("/{record_id}", response_model=StaffCashoutRecordRead)
def get_cashout_record(
    record_id: int,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    loaded = _load_and_assert_gto(record_id, role, db)
    data = loaded if loaded is not None else get_staff_cashout_record(record_id)
    if not data:
        raise HTTPException(404, "Cashout record not found")
    return _to_read(data, _club_name_map(db))


@router.delete("/{record_id}", status_code=204)
def remove_cashout_record(
    record_id: int,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    _load_and_assert_gto(record_id, role, db)
    if not delete_staff_cashout_record(record_id):
        raise HTTPException(404, "Cashout record not found")
    return None


@router.patch("/{record_id}", response_model=StaffCashoutRecordRead)
def patch_cashout_record(
    record_id: int,
    body: StaffCashoutRecordUpdate,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    _load_and_assert_gto(record_id, role, db)
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        data = get_staff_cashout_record(record_id)
        if not data:
            raise HTTPException(404, "Cashout record not found")
        return _to_read(data, _club_name_map(db))

    if "do_not_send" in updates and role != ROLE_ADMIN:
        raise HTTPException(403, "Admin only")
    if "audited" in updates and role != ROLE_ADMIN:
        raise HTTPException(403, "Admin only")

    try:
        data = update_staff_cashout_record(
            record_id,
            group_title=updates.get("group_title"),
            amount=updates.get("amount"),
            sending=updates.get("sending") if "sending" in updates else None,
            do_not_send=updates.get("do_not_send")
            if "do_not_send" in updates
            else None,
            audited=updates.get("audited") if "audited" in updates else None,
        )
    except CashoutRecordNotActive as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not data:
        raise HTTPException(404, "Cashout record not found")
    return _to_read(data, _club_name_map(db))


@router.put("/{record_id}/payments", response_model=StaffCashoutRecordRead)
def replace_payments(
    record_id: int,
    body: List[StaffCashoutPaymentCreate],
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    _load_and_assert_gto(record_id, role, db)
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


@router.post(
    "/{record_id}/payments", response_model=StaffCashoutRecordRead, status_code=201
)
def add_payment(
    record_id: int,
    body: StaffCashoutPaymentCreate,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    _load_and_assert_gto(record_id, role, db)
    try:
        data = add_staff_cashout_payment(record_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not data:
        raise HTTPException(404, "Cashout record not found")
    return _to_read(data, _club_name_map(db))


@router.patch(
    "/{record_id}/payments/{payment_id}", response_model=StaffCashoutRecordRead
)
def patch_payment(
    record_id: int,
    payment_id: int,
    body: StaffCashoutPaymentUpdate,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    _load_and_assert_gto(record_id, role, db)
    updates = body.model_dump(exclude_unset=True)
    try:
        data = update_staff_cashout_payment(record_id, payment_id, updates)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not data:
        raise HTTPException(404, "Cashout record or payment not found")
    return _to_read(data, _club_name_map(db))


@router.delete(
    "/{record_id}/payments/{payment_id}", response_model=StaffCashoutRecordRead
)
def remove_payment(
    record_id: int,
    payment_id: int,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    _load_and_assert_gto(record_id, role, db)
    data = delete_staff_cashout_payment(record_id, payment_id)
    if not data:
        raise HTTPException(404, "Cashout record or payment not found")
    return _to_read(data, _club_name_map(db))


@router.post(
    "/{record_id}/sends", response_model=StaffCashoutRecordRead, status_code=201
)
def add_send(
    record_id: int,
    body: StaffCashoutSendCreate,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    _load_and_assert_gto(record_id, role, db)
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
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    _load_and_assert_gto(record_id, role, db)
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
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    _load_and_assert_gto(record_id, role, db)
    data = delete_staff_cashout_send(record_id, send_id)
    if not data:
        raise HTTPException(404, "Cashout record or send not found")
    return _to_read(data, _club_name_map(db))
