"""Group handlers for player popup reply keyboard."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from bot.handlers.flow_cancel import cashout_flow_active, deposit_flow_active
from bot.services.club import get_club_for_chat
from bot.services import popup_keyboard as pk

logger = logging.getLogger(__name__)


async def popup_keyboard_activity_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Track human group activity: upsert player id, strip if needed, (re)schedule idle."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user:
        return
    if chat.type not in ("group", "supergroup"):
        return
    if user.is_bot:
        return

    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        return
    if not pk.popup_keyboard_eligible(chat.id, club_id=club_id, title=chat.title):
        return

    if not pk.is_support_sender(user, club_id):
        pk.upsert_player_telegram_user_id(
            chat.id, user.id, username=user.username
        )
        pk.remember_player_message(
            context,
            user_id=user.id,
            message_id=message.message_id,
            username=user.username,
        )

        # Free text / media (not deposit/cashout) while keyboard is up → silent strip.
        if not pk.is_flow_command_text(message.text) and not (
            deposit_flow_active(context) or cashout_flow_active(context)
        ):
            await pk.silent_strip_if_installed(
                context.bot, chat.id, context=context
            )

    # While deposit/cashout is active, only cancel idle — restore after flow exit.
    if deposit_flow_active(context) or cashout_flow_active(context):
        pk.cancel_popup_keyboard_idle(
            chat.id, job_queue=getattr(context, "job_queue", None)
        )
        return

    pk.schedule_popup_keyboard_idle(context, chat.id)


def get_popup_keyboard_activity_handler() -> MessageHandler:
    return MessageHandler(
        filters.ChatType.GROUPS & filters.ALL & ~filters.StatusUpdate.ALL,
        popup_keyboard_activity_handler,
        block=False,
    )
