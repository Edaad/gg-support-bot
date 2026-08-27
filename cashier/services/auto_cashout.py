"""Complete a fully automated player cashout via the shared GGCashier hub path.

The player-facing bot collects amount, method, sub-option, and a validated payout
handle, then calls :func:`complete_auto_cashout`. This creates a
``cashier_cashout_jobs`` row (trigger ``auto_cashout``), fills in the payment
fields, marks the trade-record / 24-hour attestations (both are enforced by the
bot before this point), and runs the same ``complete_cashout_job`` used by staff
cashouts so the row reaches Zapier -> Glide, the audit table, the group owed pin
+ ASAP message, and the cooldown activity.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from cashier.services.complete import complete_cashout_job
from cashier.services.jobs import create_job, update_job

logger = logging.getLogger(__name__)


async def complete_auto_cashout(
    *,
    club_id: int,
    chat_id: int,
    group_title: str,
    amount: Decimal,
    initiated_by: int,
    payment_method_id: int,
    payment_sub_option_id: Optional[int],
    method_display_name: str,
    payout_details: str,
) -> tuple[bool, Optional[str]]:
    """Create + complete an automated cashout job. Returns (ok, error_message)."""
    job = create_job(
        club_id=int(club_id),
        chat_id=int(chat_id),
        group_title=group_title or "Unknown group",
        amount=amount,
        initiated_by=int(initiated_by),
        trigger="auto_cashout",
    )
    job_id = int(job["id"])
    update_job(
        job_id,
        payment_method_id=int(payment_method_id),
        payment_sub_option_id=(
            int(payment_sub_option_id) if payment_sub_option_id is not None else None
        ),
        method_display_name=method_display_name,
        payout_details=payout_details,
        trade_record_checked=True,
        cooldown_checked=True,
        status="in_progress",
    )
    ok, err = await complete_cashout_job(job_id)
    if not ok:
        logger.warning(
            "auto_cashout: complete failed job_id=%s chat_id=%s err=%s",
            job_id,
            chat_id,
            err,
        )
    return ok, err
