"""Admin /checkplayer: count and list eligible human players in a group via Telethon."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from club_gc_settings import get_club_gc_config_by_link_club_id
from config import ADMIN_USER_IDS
from bot.handlers.gc_chat_id import parse_gc_chat_id_args
from bot.services.club import get_club_for_chat
from bot.services.mtproto_check_player import (
    check_players_in_group,
    format_check_player_result,
)

logger = logging.getLogger(__name__)

_USAGE = (
    "Usage: /checkplayer <chat_id>\n"
    "Example: /checkplayer -1001234567890\n"
    "Example: /checkplayer gc_id -1001234567890"
)


async def checkplayer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if update.effective_user.id not in ADMIN_USER_IDS:
        return

    chat_id = parse_gc_chat_id_args(context.args or [])
    if chat_id is None:
        await update.message.reply_text(_USAGE)
        return

    club_id = get_club_for_chat(chat_id)
    if club_id is None:
        await update.message.reply_text(
            f"Chat ID {chat_id} is not linked to a club in the dashboard.\n"
            "Link the group first, or run from a known support group."
        )
        return

    cfg = get_club_gc_config_by_link_club_id(int(club_id))
    if not cfg:
        await update.message.reply_text(
            f"No MTProto profile for club_id={club_id}. Cannot run Telethon check."
        )
        return

    try:
        result = await check_players_in_group(cfg, chat_id)
    except Exception:
        logger.exception("checkplayer failed chat_id=%s club_id=%s", chat_id, club_id)
        await update.message.reply_text("Check failed (see worker logs).")
        return

    await update.message.reply_text(format_check_player_result(result))
