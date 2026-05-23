"""Admin /telemsg: show what the club Telethon session sees as the latest group message."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from club_gc_settings import (
    gc_mtproto_operator_telegram_user_ids,
    get_club_gc_config_by_link_club_id,
)
from config import ADMIN_USER_IDS
from bot.services.club import get_club_for_chat, is_club_staff
from bot.services.mtproto_dm_gc_listener import get_dm_gc_listener_status
from bot.services.mtproto_latest_message import (
    fetch_telethon_latest_messages,
    format_telethon_latest_check,
)

logger = logging.getLogger(__name__)


def _can_use_telemsg(user_id: int, club_id: int) -> bool:
    if user_id in ADMIN_USER_IDS or user_id in gc_mtproto_operator_telegram_user_ids():
        return True
    if is_club_staff(user_id, club_id):
        return True
    cfg = get_club_gc_config_by_link_club_id(club_id)
    return cfg is not None and int(cfg.command_admin_user_id) == int(user_id)


async def telemsg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use /telemsg in a linked support group.")
        return

    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        await update.message.reply_text("This group isn't linked to a club.")
        return

    user_id = update.effective_user.id
    if not _can_use_telemsg(user_id, club_id):
        return

    cfg = get_club_gc_config_by_link_club_id(club_id)
    if not cfg:
        await update.message.reply_text(
            "No MTProto profile is configured for this club's dashboard id."
        )
        return

    try:
        result = await fetch_telethon_latest_messages(cfg, chat.id, limit=3)
    except Exception:
        logger.exception(
            "telemsg: fetch failed club_id=%s chat_id=%s", club_id, chat.id
        )
        await update.message.reply_text("Telethon check failed (see worker logs).")
        return

    text = format_telethon_latest_check(
        result,
        bot_command_message_id=update.message.message_id,
        listener_status=get_dm_gc_listener_status(),
    )
    await update.message.reply_text(text)
