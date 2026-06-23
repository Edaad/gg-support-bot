"""DM staff to continue an issue report wizard after /report in a group."""

from __future__ import annotations

import logging

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


async def notify_staff_issue_report_draft(
    bot: Bot,
    *,
    staff_user_id: int,
    draft_id: int,
    group_title: str | None,
) -> bool:
    group_line = group_title or "Unknown group"
    text = (
        "Issue report started\n"
        f"Group: {group_line}\n\n"
        "Tap below to continue the report in this chat."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Continue report", callback_data=f"ir_draft:{draft_id}"
                ),
                InlineKeyboardButton(
                    "CANCEL", callback_data=f"ir_draft_cancel:{draft_id}"
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
            "notify_staff_issue_report_draft failed draft_id=%s staff=%s",
            draft_id,
            staff_user_id,
        )
        return False
