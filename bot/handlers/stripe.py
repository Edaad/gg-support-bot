"""Group command: /stripe — disabled. Use /deposit instead."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

STRIPE_COMMAND_DISABLED_MESSAGE = (
    "This command is no longer available. Use /deposit in your support group."
)


async def stripe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(STRIPE_COMMAND_DISABLED_MESSAGE)
