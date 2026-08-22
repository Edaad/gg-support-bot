"""Complete a cashier cashout job: Zapier, owed pin/ASAP, cooldown."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

from bot.services.club import (
    get_club_by_id,
    invalidate_pending_one_time_bypasses,
    record_activity_for_chat,
)
from bot.services.mtproto_group_cash import schedule_cash_flow_from_club
from bot.services.slack_ops_notify import notify_slack_head_admin_escalation
from bot.services.staff_cashout_records import (
    apply_low_deposit_cashout_hold,
    create_staff_cashout_record_from_job,
)
from cashier.services.jobs import complete_job, get_job
from cashier.services.zapier import fire_zapier_webhook

logger = logging.getLogger(__name__)

_HOLD_REASON_LABELS = {
    "no_deposits": "0 deposits in this group",
    "single_deposit": "1 deposit in this group",
    "count_failed": "could not verify deposit count",
}


def _format_low_deposit_hold_slack(hold: dict[str, Any]) -> str:
    club = get_club_by_id(int(hold["club_id"]))
    club_name = (club.name if club else None) or f"club_id={hold['club_id']}"
    gg_player_id = hold.get("gg_player_id") or "—"
    amount = hold.get("amount")
    amount_str = str(amount) if amount is not None else "—"
    reason = str(hold.get("reason") or "")
    reason_label = _HOLD_REASON_LABELS.get(reason, reason or "low deposit hold")
    deposit_count = hold.get("deposit_count")
    if deposit_count is None:
        count_line = "Deposit count: could not verify"
    else:
        count_line = f"Deposit count: {deposit_count}"
    return (
        "Low-deposit cashout hold — do not send until admin clears.\n"
        f"Club: {club_name}\n"
        f"Group: {hold.get('group_title') or '—'}\n"
        f"GG player id: {gg_player_id}\n"
        f"Amount: {amount_str}\n"
        f"{count_line}\n"
        f"Reason: {reason_label}\n"
        f"Record id: {hold.get('record_id')}\n"
        f"Chat id: {hold.get('chat_id')}"
    )


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

    try:
        record_id = create_staff_cashout_record_from_job(job)
    except Exception:
        logger.exception(
            "complete_cashout_job: staff_cashout_record failed job_id=%s",
            job_id,
        )
        record_id = None

    if record_id:
        try:
            hold = apply_low_deposit_cashout_hold(record_id)
            if hold:
                await notify_slack_head_admin_escalation(
                    _format_low_deposit_hold_slack(hold),
                    source="low_deposit_cashout",
                )
        except Exception:
            logger.exception(
                "complete_cashout_job: low_deposit_hold failed job_id=%s "
                "record_id=%s",
                job_id,
                record_id,
            )

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
