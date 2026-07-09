"""Admin /add: record a manual add for a player and reset cashout cooldown."""

from __future__ import annotations

import logging
import re
from decimal import Decimal

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_USER_IDS
from club_gc_settings import get_club_config_for_admin, is_dm_gc_listener_enabled
from bot.handlers.deposit import cancel_deposit_reminder
from bot.services.club import (
    get_club_allows_admin_commands,
    get_club_for_chat,
    invalidate_pending_one_time_bypasses,
    is_club_staff,
    record_activity_for_chat,
)
from bot.services.agent_debug_log import agent_debug_log
from bot.services.mtproto_bot_fallback import bot_delete_message, telethon_missed_command_message
from bot.services.mtproto_dm_gc_listener import _clients, get_dm_gc_listener_status
from bot.services.mtproto_group_add import (
    format_add_confirmation,
    parse_add_command,
    schedule_send_add_confirmation_from_club,
)
from bot.services.bonus_from_add import maybe_start_bonus_recording_from_add

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


def _schedule_auto_chip_add(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    club_id: int,
    message_id: int,
    amount: Decimal,
    bonus: Decimal | None,
    group_title: str | None,
) -> None:
    """Fire optional ClubGG auto chip-add (no-op unless enabled + configured)."""
    from bot.services.clubgg_deposit_api import trigger_auto_chip_add

    context.application.create_task(
        trigger_auto_chip_add(
            club_id=club_id,
            chat_id=chat_id,
            message_id=message_id,
            amount=amount,
            bonus=bonus,
            group_title=group_title,
            ptb_bot=context.bot,
        ),
        name=f"auto-chip-add-{chat_id}",
    )


async def _add_bot_api_path(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    club_id: int,
    amount: Decimal,
    bonus: Decimal | None,
    name: str | None,
    command_already_deleted: bool = False,
) -> None:
    chat = update.effective_chat
    assert chat is not None and update.message is not None

    confirmation = format_add_confirmation(amount, bonus, name=name)

    if not command_already_deleted:
        deleted = await bot_delete_message(
            context.bot,
            chat_id=chat.id,
            message_id=update.message.message_id,
        )
        if not deleted:
            logger.warning(
                "add: could not delete command message chat_id=%s message_id=%s",
                chat.id,
                update.message.message_id,
            )

    schedule_send_add_confirmation_from_club(
        chat_id=chat.id,
        club_id=club_id,
        text=confirmation,
    )

    _schedule_auto_chip_add(
        context,
        chat_id=chat.id,
        club_id=club_id,
        message_id=update.message.message_id,
        amount=amount,
        bonus=bonus,
        group_title=chat.title,
    )

    if bonus is not None:
        context.application.create_task(
            maybe_start_bonus_recording_from_add(
                context.bot,
                staff_user_id=update.effective_user.id,
                club_id=club_id,
                chat_id=chat.id,
                group_title=chat.title,
                bonus_amount=bonus,
            ),
            name=f"bonus-from-add-{chat.id}",
        )


async def _add_telethon_fallback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    club_id: int,
    amount: Decimal,
    bonus: Decimal | None,
    name: str | None,
) -> None:
    chat = update.effective_chat
    assert chat is not None and update.message is not None

    missed = await telethon_missed_command_message(
        context.bot,
        chat_id=chat.id,
        message_id=update.message.message_id,
        command="/add",
    )
    if not missed:
        return

    await _add_bot_api_path(
        update,
        context,
        club_id=club_id,
        amount=amount,
        bonus=bonus,
        name=name,
        command_already_deleted=True,
    )


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

    cancel_deposit_reminder(context, chat.id)

    try:
        record_activity_for_chat(club_id, chat.id, "deposit")
        record_activity_for_chat(club_id, chat.id, "add_cmd")
        invalidate_pending_one_time_bypasses(club_id, chat.id)
    except Exception:
        logger.exception(
            "add: record_activity failed club_id=%s chat_id=%s",
            club_id,
            chat.id,
        )

    if get_club_config_for_admin(admin_id) and is_dm_gc_listener_enabled():
        # #region agent log
        _club_cfg = get_club_config_for_admin(admin_id)
        _conn = {
            getattr(c, "_gg_club_key", "?"): c.is_connected() for c in _clients
        }
        agent_debug_log(
            hypothesis_id="B",
            location="add.py:_execute_add",
            message="bot_api_delegating_to_telethon_with_fallback",
            data={
                "admin_id": admin_id,
                "club_id": club_id,
                "chat_id": chat.id,
                "club_key": getattr(_club_cfg, "club_key", None),
                "listener_status": get_dm_gc_listener_status(),
                "client_connected": _conn,
            },
        )
        # #endregion
        context.application.create_task(
            _add_telethon_fallback(
                update,
                context,
                club_id=club_id,
                amount=amount,
                bonus=bonus,
                name=name,
            ),
            name=f"add-telethon-fallback-{chat.id}",
        )
        return

    await _add_bot_api_path(
        update,
        context,
        club_id=club_id,
        amount=amount,
        bonus=bonus,
        name=name,
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
    if not re.fullmatch(r"\d+(?:\.\d+)?", cmd):
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
