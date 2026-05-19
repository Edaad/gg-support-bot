"""Admin /add: record a manual add for a player and reset cashout cooldown."""

from __future__ import annotations

import logging
import random
from decimal import Decimal, InvalidOperation

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_USER_IDS
from bot.services.club import (
    get_club_allows_admin_commands,
    get_club_for_chat,
    is_club_staff,
    record_activity,
)

logger = logging.getLogger(__name__)

ADD_CONFIRMATION_MESSAGES = (
    "good luck",
    "best of luck",
    "have fun at the tables",
    "best of luck at the tables",
    "enjoy",
)


def _can_use_add(user_id: int, club_id: int) -> bool:
    if is_club_staff(user_id, club_id):
        return True
    if user_id in ADMIN_USER_IDS:
        return get_club_allows_admin_commands(club_id)
    return False


def _parse_amount(args: list[str]) -> Decimal | None:
    if not args:
        return None
    raw = args[0].strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(raw)
        if amount <= 0:
            return None
        return amount
    except (InvalidOperation, Exception):
        return None


async def add_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return

    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        await update.message.reply_text("This group isn't linked to a club.")
        return

    admin_id = update.effective_user.id
    if not _can_use_add(admin_id, club_id):
        return

    amount = _parse_amount(context.args or [])
    if amount is None:
        await update.message.reply_text(
            "Usage: reply to the player's message with /add <amount> (Example: /add 500)"
        )
        return

    reply = update.message.reply_to_message
    if not reply or not reply.from_user:
        await update.message.reply_text(
            "Reply to the player's message with /add <amount> (Example: /add 500)"
        )
        return

    target_user = reply.from_user
    if target_user.is_bot:
        await update.message.reply_text("Cannot add balance for a bot.")
        return

    phrase = random.choice(ADD_CONFIRMATION_MESSAGES)
    confirmation = f"Added ${amount:,.2f}, {phrase}!!"

    try:
        await context.bot.delete_message(
            chat_id=chat.id, message_id=update.message.message_id
        )
    except Exception:
        logger.warning(
            "add: could not delete command message chat_id=%s message_id=%s",
            chat.id,
            update.message.message_id,
            exc_info=True,
        )

    await context.bot.send_message(chat_id=chat.id, text=confirmation)

    try:
        record_activity(club_id, target_user.id, chat.id, "deposit")
    except Exception:
        logger.exception(
            "add: record_activity failed club_id=%s user_id=%s chat_id=%s",
            club_id,
            target_user.id,
            chat.id,
        )
