"""CRUD helpers for staff_cashout_records, destinations, and money sends."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from bot.services.club import get_method_by_id, get_sub_option_by_id
from bot.services.player_details import parse_tracking_title
from db.connection import get_db
from db.models import Club, StaffCashoutMoneySend, StaffCashoutPayment, StaffCashoutRecord

logger = logging.getLogger(__name__)

STATUSES = ("active", "cleared", "oversent")


class CashoutRecordNotActive(ValueError):
    """Original amount may only change while the cashout is active."""


def _gg_player_id_from_title(group_title: str) -> Optional[str]:
    parsed = parse_tracking_title(group_title)
    if not parsed:
        return None
    return parsed[1]


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or 0))


def _payment_to_dict(payment: StaffCashoutPayment) -> dict[str, Any]:
    return {
        "id": payment.id,
        "cashout_record_id": payment.cashout_record_id,
        "payment_method_id": payment.payment_method_id,
        "payment_sub_option_id": payment.payment_sub_option_id,
        "method_display_name": payment.method_display_name,
        "payout_details": payment.payout_details,
        "amount": payment.amount,
        "sort_order": payment.sort_order,
        "created_at": payment.created_at,
    }


def _send_to_dict(row: StaffCashoutMoneySend) -> dict[str, Any]:
    return {
        "id": row.id,
        "cashout_record_id": row.cashout_record_id,
        "sender_name": row.sender_name,
        "amount": row.amount,
        "payment_method_id": row.payment_method_id,
        "payment_sub_option_id": row.payment_sub_option_id,
        "method_display_name": row.method_display_name,
        "created_at": row.created_at,
    }


def compute_ledger(tracks_money_sent: bool, original: Any, sends: list[dict[str, Any]]) -> dict[str, Any]:
    original_amt = _as_decimal(original)
    if not tracks_money_sent:
        return {
            "tracks_money_sent": False,
            "sent": Decimal("0"),
            "remaining": Decimal("0"),
            "status": "cleared",
        }
    sent = sum((_as_decimal(s.get("amount")) for s in sends), Decimal("0"))
    remaining = original_amt - sent
    if sent < original_amt:
        status = "active"
    elif sent == original_amt:
        status = "cleared"
    else:
        status = "oversent"
    return {
        "tracks_money_sent": True,
        "sent": sent,
        "remaining": remaining,
        "status": status,
    }


def _record_to_dict(record: StaffCashoutRecord) -> dict[str, Any]:
    payments = [_payment_to_dict(p) for p in sorted(record.payments, key=lambda p: p.sort_order)]
    sends = [_send_to_dict(s) for s in sorted(record.money_sends, key=lambda r: r.created_at or datetime.min)]
    tracks = bool(record.tracks_money_sent)
    ledger = compute_ledger(tracks, record.amount, sends)
    return {
        "id": record.id,
        "cashier_job_id": record.cashier_job_id,
        "club_id": record.club_id,
        "chat_id": record.chat_id,
        "group_title": record.group_title,
        "gg_player_id": record.gg_player_id,
        "amount": record.amount,
        "recorded_by_telegram_user_id": record.recorded_by_telegram_user_id,
        "trigger": record.trigger,
        "tracks_money_sent": tracks,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "payments": payments,
        "sends": sends,
        **ledger,
    }


def _record_dict_reloaded(session, record: StaffCashoutRecord) -> dict[str, Any]:
    """Flush then reload collections so add/delete show up in the API response."""
    session.flush()
    session.expire(record, ["payments", "money_sends"])
    return _record_to_dict(record)


def _validate_method_choice(
    *,
    payment_method_id: Any,
    payment_sub_option_id: Any,
    method_display_name: Any,
    payout_details: Any = None,
    require_payout_details: bool,
) -> tuple[Optional[int], Optional[int], str, Optional[str]]:
    method_id = int(payment_method_id) if payment_method_id is not None else None
    sub_id = int(payment_sub_option_id) if payment_sub_option_id is not None else None
    display = (method_display_name or "").strip()
    details = (payout_details or "").strip() if payout_details is not None else ""

    if method_id is None:
        if not display:
            raise ValueError("Custom method name is required")
        if require_payout_details:
            pass
        return None, None, display, (details or None)

    method = get_method_by_id(method_id)
    if not method:
        raise ValueError("Payment method not found")
    display = (method.get("name") or display or "").strip()
    if not display:
        raise ValueError("Method name is required")
    if method.get("has_sub_options"):
        if sub_id is None:
            raise ValueError("Sub-option is required for this method")
        sub = get_sub_option_by_id(sub_id)
        if not sub:
            raise ValueError("Sub-option not found")
        sub_name = (sub.get("name") or "").strip()
        if sub_name:
            display = f"{display} / {sub_name}"
    else:
        sub_id = None
    if require_payout_details and not details:
        raise ValueError("Payout details are required for this method")
    return method_id, sub_id, display, (details or None)


def get_staff_cashout_record(record_id: int) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        return _record_to_dict(record)


def get_staff_cashout_record_by_job_id(cashier_job_id: int) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = (
            session.query(StaffCashoutRecord)
            .filter(StaffCashoutRecord.cashier_job_id == int(cashier_job_id))
            .first()
        )
        if not record:
            return None
        return _record_to_dict(record)


def create_staff_cashout_record_from_job(job: dict[str, Any]) -> Optional[int]:
    """Create order + first destination. New rows track money sent (start at $0)."""
    job_id = job.get("id")
    if job_id is None:
        return None

    with get_db() as session:
        existing = (
            session.query(StaffCashoutRecord)
            .filter(StaffCashoutRecord.cashier_job_id == int(job_id))
            .first()
        )
        if existing:
            logger.info(
                "staff_cashout_record already exists job_id=%s record_id=%s",
                job_id,
                existing.id,
            )
            return existing.id

        group_title = job.get("group_title") or ""
        amount = job.get("amount")
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount or 0))

        record = StaffCashoutRecord(
            cashier_job_id=int(job_id),
            club_id=int(job["club_id"]),
            chat_id=int(job["chat_id"]),
            group_title=group_title,
            gg_player_id=_gg_player_id_from_title(group_title),
            amount=amount,
            recorded_by_telegram_user_id=int(job["initiated_by"]),
            trigger=str(job.get("trigger") or "group_cash"),
            tracks_money_sent=True,
        )
        session.add(record)
        session.flush()

        payment = StaffCashoutPayment(
            cashout_record_id=record.id,
            payment_method_id=job.get("payment_method_id"),
            payment_sub_option_id=job.get("payment_sub_option_id"),
            method_display_name=job.get("method_display_name"),
            payout_details=job.get("payout_details"),
            amount=None,
            sort_order=0,
        )
        session.add(payment)
        session.flush()
        logger.info(
            "staff_cashout_record created job_id=%s record_id=%s",
            job_id,
            record.id,
        )
        return record.id


def create_staff_cashout_record_manual(
    *,
    club_id: int,
    group_title: str,
    amount: Decimal,
) -> dict[str, Any]:
    title = (group_title or "").strip()
    if not title:
        raise ValueError("Name is required")
    amt = _as_decimal(amount)
    if amt <= 0:
        raise ValueError("Amount must be greater than zero")

    with get_db() as session:
        club = session.get(Club, int(club_id))
        if not club:
            raise ValueError("Club not found")
        record = StaffCashoutRecord(
            cashier_job_id=None,
            club_id=int(club_id),
            chat_id=None,
            group_title=title,
            gg_player_id=_gg_player_id_from_title(title),
            amount=amt,
            recorded_by_telegram_user_id=None,
            trigger="dashboard",
            tracks_money_sent=True,
        )
        session.add(record)
        session.flush()
        logger.info("staff_cashout_record created from dashboard record_id=%s", record.id)
        session.expire(record, ["payments", "money_sends"])
        return _record_to_dict(record)


def update_staff_cashout_record(
    record_id: int,
    *,
    group_title: Optional[str] = None,
    amount: Optional[Decimal] = None,
) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        current = _record_to_dict(record)
        if amount is not None and current["status"] != "active":
            raise CashoutRecordNotActive("Original amount can only be edited while active")
        if group_title is not None:
            record.group_title = group_title
            record.gg_player_id = _gg_player_id_from_title(group_title)
        if amount is not None:
            record.amount = amount
        record.updated_at = datetime.utcnow()
        return _record_dict_reloaded(session, record)


def replace_staff_cashout_payments(
    record_id: int, payments: list[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None

        session.query(StaffCashoutPayment).filter(
            StaffCashoutPayment.cashout_record_id == int(record_id)
        ).delete(synchronize_session=False)

        for idx, pdata in enumerate(payments):
            method_id, sub_id, display, details = _validate_method_choice(
                payment_method_id=pdata.get("payment_method_id"),
                payment_sub_option_id=pdata.get("payment_sub_option_id"),
                method_display_name=pdata.get("method_display_name"),
                payout_details=pdata.get("payout_details"),
                require_payout_details=pdata.get("payment_method_id") is not None,
            )
            session.add(
                StaffCashoutPayment(
                    cashout_record_id=int(record_id),
                    payment_method_id=method_id,
                    payment_sub_option_id=sub_id,
                    method_display_name=display,
                    payout_details=details,
                    amount=pdata.get("amount"),
                    sort_order=pdata.get("sort_order", idx),
                )
            )
        record.updated_at = datetime.utcnow()
        return _record_dict_reloaded(session, record)


def add_staff_cashout_payment(
    record_id: int, pdata: dict[str, Any]
) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        method_id, sub_id, display, details = _validate_method_choice(
            payment_method_id=pdata.get("payment_method_id"),
            payment_sub_option_id=pdata.get("payment_sub_option_id"),
            method_display_name=pdata.get("method_display_name"),
            payout_details=pdata.get("payout_details"),
            require_payout_details=pdata.get("payment_method_id") is not None,
        )
        max_order = max((p.sort_order for p in record.payments), default=-1)
        session.add(
            StaffCashoutPayment(
                cashout_record_id=int(record_id),
                payment_method_id=method_id,
                payment_sub_option_id=sub_id,
                method_display_name=display,
                payout_details=details,
                amount=pdata.get("amount"),
                sort_order=pdata.get("sort_order", max_order + 1),
            )
        )
        record.updated_at = datetime.utcnow()
        return _record_dict_reloaded(session, record)


def update_staff_cashout_payment(
    record_id: int, payment_id: int, pdata: dict[str, Any]
) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        payment = session.get(StaffCashoutPayment, int(payment_id))
        if not payment or payment.cashout_record_id != int(record_id):
            return None
        merged = {
            "payment_method_id": payment.payment_method_id,
            "payment_sub_option_id": payment.payment_sub_option_id,
            "method_display_name": payment.method_display_name,
            "payout_details": payment.payout_details,
        }
        for key in merged:
            if key in pdata:
                merged[key] = pdata[key]
        method_id, sub_id, display, details = _validate_method_choice(
            payment_method_id=merged["payment_method_id"],
            payment_sub_option_id=merged["payment_sub_option_id"],
            method_display_name=merged["method_display_name"],
            payout_details=merged["payout_details"],
            require_payout_details=merged["payment_method_id"] is not None,
        )
        payment.payment_method_id = method_id
        payment.payment_sub_option_id = sub_id
        payment.method_display_name = display
        payment.payout_details = details
        if "amount" in pdata:
            payment.amount = pdata["amount"]
        if "sort_order" in pdata and pdata["sort_order"] is not None:
            payment.sort_order = pdata["sort_order"]
        record.updated_at = datetime.utcnow()
        return _record_dict_reloaded(session, record)


def delete_staff_cashout_payment(record_id: int, payment_id: int) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        payment = session.get(StaffCashoutPayment, int(payment_id))
        if not payment or payment.cashout_record_id != int(record_id):
            return None
        session.delete(payment)
        record.updated_at = datetime.utcnow()
        return _record_dict_reloaded(session, record)


def add_staff_cashout_send(record_id: int, pdata: dict[str, Any]) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        sender = (pdata.get("sender_name") or "").strip()
        if not sender:
            raise ValueError("Name is required")
        amount = _as_decimal(pdata.get("amount"))
        if amount <= 0:
            raise ValueError("Amount must be greater than zero")
        method_id, sub_id, display, _details = _validate_method_choice(
            payment_method_id=pdata.get("payment_method_id"),
            payment_sub_option_id=pdata.get("payment_sub_option_id"),
            method_display_name=pdata.get("method_display_name"),
            payout_details=None,
            require_payout_details=False,
        )
        session.add(
            StaffCashoutMoneySend(
                cashout_record_id=int(record_id),
                sender_name=sender,
                amount=amount,
                payment_method_id=method_id,
                payment_sub_option_id=sub_id,
                method_display_name=display,
            )
        )
        record.updated_at = datetime.utcnow()
        return _record_dict_reloaded(session, record)


def update_staff_cashout_send(
    record_id: int, send_id: int, pdata: dict[str, Any]
) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        row = session.get(StaffCashoutMoneySend, int(send_id))
        if not row or row.cashout_record_id != int(record_id):
            return None
        if "sender_name" in pdata:
            sender = (pdata.get("sender_name") or "").strip()
            if not sender:
                raise ValueError("Name is required")
            row.sender_name = sender
        if "amount" in pdata and pdata["amount"] is not None:
            amount = _as_decimal(pdata.get("amount"))
            if amount <= 0:
                raise ValueError("Amount must be greater than zero")
            row.amount = amount
        method_id = row.payment_method_id if "payment_method_id" not in pdata else pdata.get("payment_method_id")
        sub_id = (
            row.payment_sub_option_id
            if "payment_sub_option_id" not in pdata
            else pdata.get("payment_sub_option_id")
        )
        display = (
            row.method_display_name
            if "method_display_name" not in pdata
            else pdata.get("method_display_name")
        )
        if any(k in pdata for k in ("payment_method_id", "payment_sub_option_id", "method_display_name")):
            method_id, sub_id, display, _d = _validate_method_choice(
                payment_method_id=method_id,
                payment_sub_option_id=sub_id,
                method_display_name=display,
                payout_details=None,
                require_payout_details=False,
            )
            row.payment_method_id = method_id
            row.payment_sub_option_id = sub_id
            row.method_display_name = display
        record.updated_at = datetime.utcnow()
        return _record_dict_reloaded(session, record)


def delete_staff_cashout_send(record_id: int, send_id: int) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        row = session.get(StaffCashoutMoneySend, int(send_id))
        if not row or row.cashout_record_id != int(record_id):
            return None
        session.delete(row)
        record.updated_at = datetime.utcnow()
        return _record_dict_reloaded(session, record)


def list_staff_cashout_records(
    *,
    club_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    if status is not None and status not in STATUSES:
        raise ValueError("status must be active, cleared, or oversent")
    with get_db() as session:
        query = session.query(StaffCashoutRecord).order_by(
            StaffCashoutRecord.created_at.desc()
        )
        if club_id is not None:
            query = query.filter(StaffCashoutRecord.club_id == int(club_id))
        rows = query.all()
        results = []
        for record in rows:
            out = _record_to_dict(record)
            if status is not None and out["status"] != status:
                continue
            results.append(out)
            if len(results) >= limit:
                break
        return results
