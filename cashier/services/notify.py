"""Notify staff via GGCashier bot DM after /cash in a support group."""

from __future__ import annotations

import logging
import os
from decimal import Decimal

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

CASHIER_BOT_TOKEN_ENV = "TELEGRAM_CASHIER_BOT_TOKEN"


async def _send_notify_error(
    staff_user_id: int, text: str, *, token: str | None
) -> None:
    """Best-effort error DM to staff when notify fails."""
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                url,
                json={"chat_id": staff_user_id, "text": f"Cashout notify failed\n\n{text}"},
            )
    except Exception:
        logger.exception(
            "notify error DM failed staff_user_id=%s",
            staff_user_id,
        )


def _format_amount(amount: Decimal) -> str:
    if amount == amount.to_integral_value():
        return f"${int(amount):,}"
    return f"${amount:,.2f}"


async def notify_staff_cashout_job(
    *,
    staff_user_id: int,
    job_id: int,
    group_title: str,
    amount: Decimal,
) -> bool:
    """Send DM to staff with button to continue the cashout wizard."""
    token = os.getenv(CASHIER_BOT_TOKEN_ENV)
    if not token:
        logger.warning("notify_staff_cashout_job: %s not set", CASHIER_BOT_TOKEN_ENV)
        await _send_notify_error(
            staff_user_id,
            "GGCashier is not configured (TELEGRAM_CASHIER_BOT_TOKEN missing). "
            "Ask an admin to set it and restart the cashier worker.",
            token=None,
        )
        return False

    text = (
        f"Cashout started\n"
        f"Group: {group_title}\n"
        f"Amount: {_format_amount(amount)}\n\n"
        f"Tap below to complete the cashout."
    )
    continue_cb = f"gc_job:{job_id}"
    cancel_cb = f"gc_job_cancel:{job_id}"
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Continue cashout", callback_data=continue_cb),
                InlineKeyboardButton("CANCEL", callback_data=cancel_cb),
            ]
        ]
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": staff_user_id,
        "text": text,
        "reply_markup": keyboard.to_dict(),
    }

    logger.info(
        "notify_staff_cashout_job sending job_id=%s staff_user_id=%s "
        "callbacks continue=%r cancel=%r",
        job_id,
        staff_user_id,
        continue_cb,
        cancel_cb,
    )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                err = data.get("description") or "Unknown Telegram error"
                logger.warning(
                    "notify_staff_cashout_job failed job_id=%s: %s",
                    job_id,
                    err,
                )
                await _send_notify_error(
                    staff_user_id,
                    f"Could not send cashout prompt for job #{job_id}: {err}",
                    token=token,
                )
                return False
            result = data.get("result") or {}
            logger.info(
                "notify_staff_cashout_job ok job_id=%s staff_user_id=%s "
                "telegram_message_id=%s",
                job_id,
                staff_user_id,
                result.get("message_id"),
            )
        return True
    except Exception as exc:
        logger.exception(
            "notify_staff_cashout_job failed job_id=%s staff=%s",
            job_id,
            staff_user_id,
        )
        await _send_notify_error(
            staff_user_id,
            f"Could not send cashout prompt for job #{job_id}: {type(exc).__name__}",
            token=token,
        )
        return False
