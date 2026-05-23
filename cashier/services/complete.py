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
    logger.info("complete_cashout_job start job_id=%s", job_id)
    job = get_job(job_id)
    if not job:
        logger.warning("complete_cashout_job: job not found id=%s", job_id)
        return False, "Job not found."
    if job["status"] == "completed":
        logger.info("complete_cashout_job: already completed id=%s", job_id)
        return True, None
    if job["status"] == "cancelled":
        logger.warning("complete_cashout_job: job cancelled id=%s", job_id)
        return False, "Job was cancelled."

    ok, zap_err = await fire_zapier_webhook(job)
    if not ok:
        logger.warning(
            "complete_cashout_job: zapier failed id=%s err=%s",
            job_id,
            zap_err,
        )
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
        logger.info(
            "complete_cashout_job: owed flow scheduled job_id=%s chat_id=%s amount=%s",
            job_id,
            chat_id,
            amount,
        )
    except Exception:
        logger.exception(
            "complete_cashout_job: schedule_cash_flow failed job_id=%s",
            job_id,
        )

    try:
        record_activity_for_chat(club_id, chat_id, "cashout")
        invalidate_pending_one_time_bypasses(club_id, chat_id)
        logger.info(
            "complete_cashout_job: cooldown recorded job_id=%s club_id=%s chat_id=%s",
            job_id,
            club_id,
            chat_id,
        )
    except Exception:
        logger.exception(
            "complete_cashout_job: record_activity failed job_id=%s",
            job_id,
        )

    complete_job(job_id)
    logger.info(
        "complete_cashout_job done job_id=%s method=%s trigger=%s",
        job_id,
        job.get("method_display_name"),
        job.get("trigger"),
    )
    return True, None
