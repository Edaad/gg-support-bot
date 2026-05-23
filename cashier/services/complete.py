"""Complete a cashier cashout job: Zapier, owed pin/ASAP, cooldown."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from bot.services.club import (
    invalidate_pending_one_time_bypasses,
    record_activity_for_chat,
)
from bot.services.mtproto_group_cash import schedule_cash_flow_from_club
from cashier.services.jobs import complete_job, get_job
from cashier.services.zapier import fire_zapier_webhook

logger = logging.getLogger(__name__)


async def complete_cashout_job(job_id: int) -> tuple[bool, Optional[str]]:
    """Finalize job: Zapier POST, pin owed + ASAP, record cooldown."""
    job = get_job(job_id)
    if not job:
        return False, "Job not found."
    if job["status"] == "completed":
        return True, None
    if job["status"] == "cancelled":
        return False, "Job was cancelled."

    ok, zap_err = await fire_zapier_webhook(job)
    if not ok:
        return False, zap_err

    club_id = int(job["club_id"])
    chat_id = int(job["chat_id"])
    amount = job["amount"]
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))

    try:
        schedule_cash_flow_from_club(
            chat_id=chat_id,
            club_id=club_id,
            amount=amount,
        )
    except Exception:
        logger.exception(
            "complete_cashout_job: schedule_cash_flow failed job_id=%s",
            job_id,
        )

    try:
        record_activity_for_chat(club_id, chat_id, "cashout")
        invalidate_pending_one_time_bypasses(club_id, chat_id)
    except Exception:
        logger.exception(
            "complete_cashout_job: record_activity failed job_id=%s",
            job_id,
        )

    complete_job(job_id)
    return True, None
