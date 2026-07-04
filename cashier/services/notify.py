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


async def notify_staff_claim_waiting(
    *,
    staff_user_id: int,
    job_id: int,
    group_title: str,
    amount: Decimal,
    player_id: str | None = None,
) -> int | None:
    """DM staff that chips are being claimed back; returns the message id (to edit later)."""
    token = os.getenv(CASHIER_BOT_TOKEN_ENV)
    if not token:
        logger.warning("notify_staff_claim_waiting: %s not set", CASHIER_BOT_TOKEN_ENV)
        return None

    who = f" from player {player_id}" if player_id else ""
    text = (
        f"\u23f3 Claiming {_format_amount(amount)} back{who} on ClubGG\u2026\n"
        f"Group: {group_title}\n\n"
        f"Please wait \u2014 I'll show the Continue button once chips are claimed."
    )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("CANCEL", callback_data=f"gc_job_cancel:{job_id}")]]
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
                    "notify_staff_claim_waiting failed job_id=%s: %s",
                    job_id,
                    data.get("description"),
                )
                return None
            return (data.get("result") or {}).get("message_id")
    except Exception:
        logger.exception(
            "notify_staff_claim_waiting failed job_id=%s staff=%s",
            job_id,
            staff_user_id,
        )
        return None


async def notify_staff_cashout_job(
    *,
    staff_user_id: int,
    job_id: int,
    group_title: str,
    amount: Decimal,
    note: str | None = None,
    edit_message_id: int | None = None,
) -> bool:
    """Send (or edit) a DM to staff with a button to continue the cashout wizard.

    ``note`` prepends a status line (e.g. claim result). ``edit_message_id`` edits the
    prior "claiming\u2026" message in place instead of sending a new DM (falls back to a
    fresh send if the edit fails).
    """
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

    lines = []
    if note:
        lines.append(note)
        lines.append("")
    lines.extend(
        [
            "Cashout started",
            f"Group: {group_title}",
            f"Amount: {_format_amount(amount)}",
            "",
            "Tap below to complete the cashout.",
        ]
    )
    text = "\n".join(lines)
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

    logger.info(
        "notify_staff_cashout_job sending job_id=%s staff_user_id=%s edit=%s "
        "callbacks continue=%r cancel=%r",
        job_id,
        staff_user_id,
        edit_message_id,
        continue_cb,
        cancel_cb,
    )

    async def _post(url: str, payload: dict) -> tuple[bool, dict]:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return bool(data.get("ok")), data

    send_url = f"https://api.telegram.org/bot{token}/sendMessage"
    send_payload = {
        "chat_id": staff_user_id,
        "text": text,
        "reply_markup": keyboard.to_dict(),
    }

    try:
        if edit_message_id is not None:
            edit_url = f"https://api.telegram.org/bot{token}/editMessageText"
            edit_payload = {
                "chat_id": staff_user_id,
                "message_id": edit_message_id,
                "text": text,
                "reply_markup": keyboard.to_dict(),
            }
            ok, data = await _post(edit_url, edit_payload)
            if ok:
                logger.info(
                    "notify_staff_cashout_job edited job_id=%s message_id=%s",
                    job_id,
                    edit_message_id,
                )
                return True
            logger.warning(
                "notify_staff_cashout_job edit failed job_id=%s: %s; sending fresh",
                job_id,
                data.get("description"),
            )

        ok, data = await _post(send_url, send_payload)
        if not ok:
            err = data.get("description") or "Unknown Telegram error"
            logger.warning("notify_staff_cashout_job failed job_id=%s: %s", job_id, err)
            await _send_notify_error(
                staff_user_id,
                f"Could not send cashout prompt for job #{job_id}: {err}",
                token=token,
            )
            return False
        result = data.get("result") or {}
        logger.info(
            "notify_staff_cashout_job ok job_id=%s staff_user_id=%s telegram_message_id=%s",
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
