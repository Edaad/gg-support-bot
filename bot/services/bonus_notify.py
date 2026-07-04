"""DM staff to continue bonus recording after /add with a bonus amount."""

from __future__ import annotations

import logging
from decimal import Decimal

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


def _format_amount(amount: Decimal) -> str:
    if amount == amount.to_integral_value():
        return f"${int(amount):,}"
    return f"${amount:,.2f}"


async def notify_staff_bonus_draft(
    bot: Bot,
    *,
    staff_user_id: int,
    draft_id: int,
    group_title: str | None,
    amount: Decimal,
    player_username: str | None = None,
) -> bool:
    group_line = group_title or "Unknown group"
    lines = [
        "Bonus to record",
        f"Group: {group_line}",
        f"Amount: {_format_amount(amount)}",
    ]
    if player_username:
        lines.append(f"Player: {player_username}")
    lines.append("")
    lines.append("Tap below to complete the bonus recording.")
    text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Continue bonus", callback_data=f"bonus_draft:{draft_id}"
                ),
                InlineKeyboardButton(
                    "CANCEL", callback_data=f"bonus_draft_cancel:{draft_id}"
                ),
            ]
        ]
    )
    try:
        await bot.send_message(
            chat_id=staff_user_id,
            text=text,
            reply_markup=keyboard,
        )
        return True
    except Exception:
        logger.exception(
            "notify_staff_bonus_draft failed draft_id=%s staff=%s",
            draft_id,
            staff_user_id,
        )
        return False
