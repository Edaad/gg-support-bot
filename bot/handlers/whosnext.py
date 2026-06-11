"""Admin /whosnext: show next pending migration recovery rows (private DM)."""

from __future__ import annotations

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from club_gc_settings import gc_mtproto_operator_telegram_user_ids
from config import ADMIN_USER_IDS
from bot.services.migration_recovery import format_whosnext_message, peek_next_recovery_rows


def _can_use_whosnext(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS or user_id in gc_mtproto_operator_telegram_user_ids()


async def whosnext_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if (
        not update.effective_user
        or not update.message
        or not update.effective_chat
        or update.effective_chat.type != ChatType.PRIVATE
    ):
        if update.effective_message:
            await update.effective_message.reply_text(
                "Use /whosnext in a private chat with this bot."
            )
        return

    user_id = int(update.effective_user.id)
    if not _can_use_whosnext(user_id):
        await update.message.reply_text("You are not allowed to use /whosnext.")
        return

    rows = peek_next_recovery_rows(limit=10)
    await update.message.reply_text(format_whosnext_message(rows))
