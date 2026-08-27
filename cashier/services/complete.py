"""Complete a cashier cashout job: Zapier, owed pin/ASAP, cooldown."""

from __future__ import annotations

import html
import logging
from decimal import Decimal
from typing import Any, Optional

from bot.services.club import (
    get_club_by_id,
    invalidate_pending_one_time_bypasses,
    record_activity_for_chat,
)
from bot.services.mtproto_group_cash import schedule_cash_flow_from_club
from bot.services.slack_ops_notify import (
    notify_slack_escalation,
    notify_slack_head_admin_escalation,
)
from bot.services.staff_cashout_records import (
    apply_low_deposit_cashout_hold,
    create_staff_cashout_record_from_job,
)
from cashier.services.jobs import complete_job, get_job
from cashier.services.notify import dm_staff
from cashier.services.zapier import fire_zapier_webhook

logger = logging.getLogger(__name__)

_HOLD_REASON_LABELS = {
    "no_deposits": "0 deposits in this group",
    "single_deposit": "1 deposit in this group",
    "count_failed": "could not verify deposit count",
}


def _hold_message_fields(hold: dict[str, Any]) -> list[tuple[str, str]]:
    club = get_club_by_id(int(hold["club_id"]))
    club_name = (club.name if club else None) or f"club_id={hold['club_id']}"
    amount = hold.get("amount")
    amount_str = str(amount) if amount is not None else "—"
    reason = str(hold.get("reason") or "")
    reason_label = _HOLD_REASON_LABELS.get(reason, reason or "low deposit hold")
    deposit_count = hold.get("deposit_count")
    if deposit_count is None:
        count_value = "could not verify"
    else:
        count_value = str(deposit_count)
    group_title = hold.get("group_title") or "—"
    return [
        ("Club", club_name),
        ("Group", group_title),
        ("Amount", amount_str),
        ("Deposit count", count_value),
        ("Reason", reason_label),
        ("Record id", str(hold.get("record_id"))),
    ]


_HOLD_HEADER = "CASHOUT ON HOLD, DO NOT SEND UNTIL HEAD ADMIN CLEARS"


def _format_low_deposit_hold_slack(hold: dict[str, Any]) -> str:
    """Slack mrkdwn: *Label*: value, group title in backticks."""
    lines = [_HOLD_HEADER]
    for label, value in _hold_message_fields(hold):
        if label == "Group":
            safe = str(value).replace("`", "'")
            lines.append(f"*{label}*: `{safe}`")
        else:
            lines.append(f"*{label}*: {value}")
    return "\n".join(lines)


def _format_low_deposit_hold_telegram(hold: dict[str, Any]) -> str:
    """Telegram HTML: <b>Label</b>: value, group title in <code>."""
    lines = [html.escape(_HOLD_HEADER)]
    for label, value in _hold_message_fields(hold):
        safe_label = html.escape(label)
        if label == "Group":
            lines.append(
                f"<b>{safe_label}</b>: <code>{html.escape(str(value))}</code>"
            )
        else:
            lines.append(f"<b>{safe_label}</b>: {html.escape(str(value))}")
    return "\n".join(lines)


async def _notify_low_deposit_hold(hold: dict[str, Any], job: dict[str, Any]) -> None:
    slack_text = _format_low_deposit_hold_slack(hold)
    await notify_slack_escalation(slack_text, source="low_deposit_cashout")
    await notify_slack_head_admin_escalation(
        slack_text, source="low_deposit_cashout"
    )
    staff_user_id = job.get("initiated_by")
    if staff_user_id is None:
        logger.warning(
            "low_deposit_hold: no initiated_by for cashier DM record_id=%s",
            hold.get("record_id"),
        )
        return
    ok = await dm_staff(
        int(staff_user_id),
        _format_low_deposit_hold_telegram(hold),
        parse_mode="HTML",
    )
    if not ok:
        logger.warning(
            "low_deposit_hold: cashier DM failed staff_user_id=%s record_id=%s",
            staff_user_id,
            hold.get("record_id"),
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
                await _notify_low_deposit_hold(hold, job)
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

    # Automated player cashouts post their own "being processed" message in-group, so
    # skip the MTProto ASAP line; still send + pin the "$X owed" message.
    send_asap = job.get("trigger") != "auto_cashout"
    try:
        schedule_cash_flow_from_club(
            chat_id=chat_id,
            club_id=club_id,
            amount=amount,
            send_asap=send_asap,
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
