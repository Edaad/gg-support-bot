"""Initiate a group /cash cashout job (shared by support bot and MTProto)."""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from cashier.services.jobs import create_job
from cashier.services.notify import notify_staff_cashout_job

logger = logging.getLogger(__name__)

WORKING_ON_CASHOUT_MESSAGE = "Working on your cashout"


def initiate_group_cash_job(
    *,
    chat_id: int,
    club_id: int,
    group_title: str,
    amount: Decimal,
    initiated_by: int,
) -> int:
    """Create job and notify staff. Returns job id."""
    job = create_job(
        club_id=club_id,
        chat_id=chat_id,
        group_title=group_title or "Unknown group",
        amount=amount,
        initiated_by=initiated_by,
        trigger="group_cash",
    )
    job_id = int(job["id"])

    async def _notify():
        await notify_staff_cashout_job(
            staff_user_id=initiated_by,
            job_id=job_id,
            group_title=group_title or "Unknown group",
            amount=amount,
        )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_notify(), name=f"notify-cashout-{job_id}")
    except RuntimeError:
        asyncio.run(_notify())

    return job_id
