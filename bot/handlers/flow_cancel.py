"""Global /cancel for /deposit and /cashout (in-progress and after conversation ends)."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

_DEPOSIT_ACTIVE_KEYS = (
    "deposit_club_id",
    "deposit_amount",
    "deposit_method_id",
    "deposit_admin_initiated",
    "deposit_simple_data",
)
_CASHOUT_ACTIVE_KEYS = (
    "cashout_club_id",
    "cashout_amount",
    "cashout_method_id",
    "cashout_admin_initiated",
    "cashout_simple_data",
)


def deposit_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return any(k in context.chat_data for k in _DEPOSIT_ACTIVE_KEYS)


def cashout_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return any(k in context.chat_data for k in _CASHOUT_ACTIVE_KEYS)


async def flow_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel an active flow, or explain when nothing is in progress."""
    if not update.message:
        return

    if deposit_flow_active(context):
        from bot.handlers.deposit import deposit_cancel

        await deposit_cancel(update, context)
        return

    if cashout_flow_active(context):
        from bot.handlers.cashout import cashout_cancel

        await cashout_cancel(update, context)
        return

    await update.message.reply_text(
        "No active deposit or cashout to cancel.\n\n"
        "If you already finished, you are all set — nothing else to do."
    )
