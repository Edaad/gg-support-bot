"""CRUD helpers for staff_cashout_records + staff_cashout_payments."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from bot.services.player_details import parse_tracking_title
from db.connection import get_db
from db.models import StaffCashoutPayment, StaffCashoutRecord

logger = logging.getLogger(__name__)


def _gg_player_id_from_title(group_title: str) -> Optional[str]:
    parsed = parse_tracking_title(group_title)
    if not parsed:
        return None
    return parsed[1]


def _record_to_dict(record: StaffCashoutRecord) -> dict[str, Any]:
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
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


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
    }


def get_staff_cashout_record(record_id: int) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        out = _record_to_dict(record)
        out["payments"] = [_payment_to_dict(p) for p in record.payments]
        return out


def get_staff_cashout_record_by_job_id(cashier_job_id: int) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = (
            session.query(StaffCashoutRecord)
            .filter(StaffCashoutRecord.cashier_job_id == int(cashier_job_id))
            .first()
        )
        if not record:
            return None
        out = _record_to_dict(record)
        out["payments"] = [_payment_to_dict(p) for p in record.payments]
        return out


def create_staff_cashout_record_from_job(job: dict[str, Any]) -> Optional[int]:
    """Create record + primary payment from completed cashier job. Idempotent on cashier_job_id."""
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
        )
        session.add(record)
        session.flush()

        payment = StaffCashoutPayment(
            cashout_record_id=record.id,
            payment_method_id=job.get("payment_method_id"),
            payment_sub_option_id=job.get("payment_sub_option_id"),
            method_display_name=job.get("method_display_name"),
            payout_details=job.get("payout_details"),
            amount=amount,
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
        if group_title is not None:
            record.group_title = group_title
            record.gg_player_id = _gg_player_id_from_title(group_title)
        if amount is not None:
            record.amount = amount
        record.updated_at = datetime.utcnow()
        session.flush()
        out = _record_to_dict(record)
        out["payments"] = [_payment_to_dict(p) for p in record.payments]
        return out


def replace_staff_cashout_payments(
    record_id: int, payments: list[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        if not payments:
            raise ValueError("At least one payment line is required")

        session.query(StaffCashoutPayment).filter(
            StaffCashoutPayment.cashout_record_id == int(record_id)
        ).delete(synchronize_session=False)

        for idx, pdata in enumerate(payments):
            session.add(
                StaffCashoutPayment(
                    cashout_record_id=int(record_id),
                    payment_method_id=pdata.get("payment_method_id"),
                    payment_sub_option_id=pdata.get("payment_sub_option_id"),
                    method_display_name=pdata.get("method_display_name"),
                    payout_details=pdata.get("payout_details"),
                    amount=pdata.get("amount"),
                    sort_order=pdata.get("sort_order", idx),
                )
            )
        record.updated_at = datetime.utcnow()
        session.flush()
        out = _record_to_dict(record)
        out["payments"] = [_payment_to_dict(p) for p in record.payments]
        return out


def add_staff_cashout_payment(
    record_id: int, pdata: dict[str, Any]
) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        max_order = max((p.sort_order for p in record.payments), default=-1)
        session.add(
            StaffCashoutPayment(
                cashout_record_id=int(record_id),
                payment_method_id=pdata.get("payment_method_id"),
                payment_sub_option_id=pdata.get("payment_sub_option_id"),
                method_display_name=pdata.get("method_display_name"),
                payout_details=pdata.get("payout_details"),
                amount=pdata.get("amount"),
                sort_order=pdata.get("sort_order", max_order + 1),
            )
        )
        record.updated_at = datetime.utcnow()
        session.flush()
        out = _record_to_dict(record)
        out["payments"] = [_payment_to_dict(p) for p in record.payments]
        return out


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
        for key in (
            "payment_method_id",
            "payment_sub_option_id",
            "method_display_name",
            "payout_details",
            "amount",
            "sort_order",
        ):
            if key in pdata and pdata[key] is not None:
                setattr(payment, key, pdata[key])
        record.updated_at = datetime.utcnow()
        session.flush()
        out = _record_to_dict(record)
        out["payments"] = [_payment_to_dict(p) for p in record.payments]
        return out


def delete_staff_cashout_payment(record_id: int, payment_id: int) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(StaffCashoutRecord, int(record_id))
        if not record:
            return None
        if len(record.payments) <= 1:
            raise ValueError("Cannot delete the last payment line")
        payment = session.get(StaffCashoutPayment, int(payment_id))
        if not payment or payment.cashout_record_id != int(record_id):
            return None
        session.delete(payment)
        record.updated_at = datetime.utcnow()
        session.flush()
        out = _record_to_dict(record)
        out["payments"] = [_payment_to_dict(p) for p in record.payments]
        return out


def list_staff_cashout_records(
    *,
    club_id: Optional[int] = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    with get_db() as session:
        query = session.query(StaffCashoutRecord).order_by(
            StaffCashoutRecord.created_at.desc()
        )
        if club_id is not None:
            query = query.filter(StaffCashoutRecord.club_id == int(club_id))
        rows = query.limit(limit).all()
        results = []
        for record in rows:
            out = _record_to_dict(record)
            payments = sorted(record.payments, key=lambda p: p.sort_order)
            out["payments"] = [_payment_to_dict(p) for p in payments]
            results.append(out)
        return results
