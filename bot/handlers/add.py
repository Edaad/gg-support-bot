"""Admin /add: record a manual add for a player and reset cashout cooldown."""

from __future__ import annotations

import logging
from decimal import Decimal

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_USER_IDS
from club_gc_settings import get_club_config_for_admin, is_dm_gc_listener_enabled
from bot.services.club import (
    get_club_allows_admin_commands,
    get_club_for_chat,
    invalidate_pending_one_time_bypasses,
    is_club_staff,
    record_activity_for_chat,
)
from bot.services.mtproto_group_add import (
    format_add_confirmation,
    parse_add_command,
    schedule_send_add_confirmation_from_club,
)

logger = logging.getLogger(__name__)


def _can_use_add(user_id: int, club_id: int) -> bool:
    if is_club_staff(user_id, club_id):
        return True
    if user_id in ADMIN_USER_IDS:
        return get_club_allows_admin_commands(club_id)
    return False


def _parse_from_args(args: list[str]) -> tuple[Decimal, Decimal | None, str | None] | None:
    if not args:
        return None
    return parse_add_command("/add " + " ".join(args))


async def _execute_add(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    club_id: int,
    amount: Decimal,
    bonus: Decimal | None,
    name: str | None,
) -> None:
    chat = update.effective_chat
    admin_id = update.effective_user.id
    assert chat is not None and update.message is not None

    try:
        record_activity_for_chat(club_id, chat.id, "deposit")
        invalidate_pending_one_time_bypasses(club_id, chat.id)
    except Exception:
        logger.exception(
            "add: record_activity failed club_id=%s chat_id=%s",
            club_id,
            chat.id,
        )

    if get_club_config_for_admin(admin_id) and is_dm_gc_listener_enabled():
        return

    confirmation = format_add_confirmation(amount, bonus, name=name)

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

    schedule_send_add_confirmation_from_club(
        chat_id=chat.id,
        club_id=club_id,
        text=confirmation,
    )


async def try_add_shorthand_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Handle /500-style add shorthand (Bot API; not a named CommandHandler)."""
    if not update.message or not update.effective_chat or not update.effective_user:
        return False

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return False

    text = update.message.text or ""
    cmd = text.split()[0].lstrip("/").split("@")[0]
    if not cmd.isdigit():
        return False

    parsed = parse_add_command(text)
    if parsed is None:
        return False

    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        return False

    admin_id = update.effective_user.id
    if not _can_use_add(admin_id, club_id):
        return False

    amount, bonus, name = parsed
    await _execute_add(
        update, context, club_id=club_id, amount=amount, bonus=bonus, name=name
    )
    return True


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

    parsed = _parse_from_args(context.args or [])
    if parsed is None:
        await update.message.reply_text(
            "Usage: /add <amount> [bonus] [name] or /<amount> "
            "(Example: /add 500, /500, /add 500 50 Jacob, /500 50 Jacob)"
        )
        return
    amount, bonus, name = parsed
    await _execute_add(
        update, context, club_id=club_id, amount=amount, bonus=bonus, name=name
    )
