"""Player /earlyrb: early rakeback request with 24h cooldown."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.services.club import (
    check_earlyrb_eligibility,
    get_club_for_chat,
    record_activity,
    update_group_name,
)

logger = logging.getLogger(__name__)

EARLYRB_ELIGIBLE_MESSAGE = (
    "We're checking your early rakeback now. Your account manager will follow up "
    "in this group shortly.\n\n"
    "Early rakeback can be requested once every 24 hours."
)

EARLYRB_RECORD_FAILED_MESSAGE = (
    "We couldn't record your early rakeback request. Please try again in a minute "
    "or message your account manager."
)


async def earlyrb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use /earlyrb in a club group.")
        return

    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        await update.message.reply_text(
            "This group isn't linked to a club yet. The club owner must add the bot."
        )
        return

    update_group_name(chat.id, chat.title)

    user_id = update.effective_user.id

    eligible, deny_msg = check_earlyrb_eligibility(club_id, chat.id)
    if not eligible:
        await update.message.reply_text(deny_msg)
        return

    try:
        record_activity(club_id, user_id, chat.id, "earlyrb")
    except Exception:
        logger.exception(
            "earlyrb: record_activity failed club_id=%s chat_id=%s user_id=%s",
            club_id,
            chat.id,
            user_id,
        )
        await update.message.reply_text(EARLYRB_RECORD_FAILED_MESSAGE)
        return

    await update.message.reply_text(EARLYRB_ELIGIBLE_MESSAGE)
