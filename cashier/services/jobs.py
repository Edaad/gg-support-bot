"""CRUD for cashier_cashout_jobs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from db.connection import get_db
from db.models import CashierCashoutJob


def _job_to_dict(job: CashierCashoutJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "club_id": job.club_id,
        "chat_id": job.chat_id,
        "group_title": job.group_title,
        "amount": job.amount,
        "payment_method_id": job.payment_method_id,
        "payment_sub_option_id": job.payment_sub_option_id,
        "method_display_name": job.method_display_name,
        "payout_details": job.payout_details,
        "trade_record_checked": bool(job.trade_record_checked),
        "cooldown_checked": bool(job.cooldown_checked),
        "initiated_by": job.initiated_by,
        "trigger": job.trigger,
        "status": job.status,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
    }


def create_job(
    *,
    club_id: int,
    chat_id: int,
    group_title: str,
    amount: Decimal,
    initiated_by: int,
    trigger: str,
) -> dict[str, Any]:
    with get_db() as session:
        job = CashierCashoutJob(
            club_id=int(club_id),
            chat_id=int(chat_id),
            group_title=group_title,
            amount=amount,
            initiated_by=int(initiated_by),
            trigger=trigger,
            status="initiated",
        )
        session.add(job)
        session.flush()
        return _job_to_dict(job)


def get_job(job_id: int) -> Optional[dict[str, Any]]:
    with get_db() as session:
        job = session.get(CashierCashoutJob, int(job_id))
        return _job_to_dict(job) if job else None


def update_job(job_id: int, **fields) -> Optional[dict[str, Any]]:
    with get_db() as session:
        job = session.get(CashierCashoutJob, int(job_id))
        if not job:
            return None
        for key, value in fields.items():
            if hasattr(job, key):
                setattr(job, key, value)
        session.flush()
        return _job_to_dict(job)


def mark_in_progress(job_id: int) -> Optional[dict[str, Any]]:
    return update_job(job_id, status="in_progress")


def cancel_job(job_id: int) -> Optional[dict[str, Any]]:
    return update_job(job_id, status="cancelled")


def complete_job(job_id: int) -> Optional[dict[str, Any]]:
    return update_job(
        job_id,
        status="completed",
        completed_at=datetime.utcnow(),
    )
