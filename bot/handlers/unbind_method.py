"""Test-bot group command: /unbindmethod — clear a payment-method link for this chat."""

from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_USER_IDS
from bot.runtime_config import is_test_bot_worker
from bot.services.club import get_club_for_chat, is_club_staff, update_group_name
from bot.services.payment_method_binding import (
    get_chat_binding,
    is_chat_method_bound,
    unbind_chat_from_method,
)

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


async def unbindmethod_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_test_bot_worker():
        return
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use /unbindmethod in a group chat.")
        return

    user_id = update.effective_user.id
    club_id = get_club_for_chat(chat.id)
    if user_id not in ADMIN_USER_IDS and (
        club_id is None or not is_club_staff(user_id, club_id)
    ):
        return

    if club_id is None:
        await update.message.reply_text("This group isn't linked to a club.")
        return

    slug = (context.args[0] if context.args else "venmo").strip().lower()
    if not _SLUG_RE.match(slug):
        await update.message.reply_text(
            "Usage: /unbindmethod [method]\n"
            "Example: /unbindmethod venmo"
        )
        return

    update_group_name(chat.id, chat.title)

    if not is_chat_method_bound(chat.id, slug):
        await update.message.reply_text(
            f"This group is not linked to {slug}. Nothing to unbind."
        )
        return

    binding = get_chat_binding(chat.id, slug)
    if not unbind_chat_from_method(chat.id, slug):
        await update.message.reply_text("Could not unbind. Please try again.")
        return

    handle = binding.venmo_handle if binding else None
    extra = f" ({handle})" if handle else ""
    logger.info(
        "unbindmethod ok chat_id=%s club_id=%s slug=%s user_id=%s",
        chat.id,
        club_id,
        slug,
        user_id,
    )
    await update.message.reply_text(
        f"Unbound {slug}{extra} for this group.\n"
        "Pending setup attempts were cancelled. "
        "The next /deposit with this method will require linking again."
    )
