"""Notify staff via GGCashier bot DM after /cash in a support group."""

from __future__ import annotations

import logging
import os
from decimal import Decimal

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

CASHIER_BOT_TOKEN_ENV = "TELEGRAM_CASHIER_BOT_TOKEN"


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
        return False

    text = (
        f"Cashout started\n"
        f"Group: {group_title}\n"
        f"Amount: {_format_amount(amount)}\n\n"
        f"Tap below to complete the cashout."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Continue cashout", callback_data=f"gc_job:{job_id}"
                ),
                InlineKeyboardButton(
                    "CANCEL", callback_data=f"gc_job_cancel:{job_id}"
                ),
            ]
        ]
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": staff_user_id,
        "text": text,
        "reply_markup": keyboard.to_dict(),
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.warning(
                    "notify_staff_cashout_job failed job_id=%s: %s",
                    job_id,
                    data.get("description"),
                )
                return False
        logger.info(
            "notify_staff_cashout_job ok job_id=%s staff_user_id=%s",
            job_id,
            staff_user_id,
        )
        return True
    except Exception:
        logger.exception(
            "notify_staff_cashout_job failed job_id=%s staff=%s",
            job_id,
            staff_user_id,
        )
        return False
