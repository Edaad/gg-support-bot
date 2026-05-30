"""Admin /cash: start GGCashier wizard (defer pin/ASAP until completion)."""

from __future__ import annotations

import logging
from decimal import Decimal

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_USER_IDS
from club_gc_settings import get_club_config_for_admin, is_dm_gc_listener_enabled
from bot.services.club import get_club_allows_admin_commands, get_club_for_chat, is_club_staff
from bot.services.agent_debug_log import agent_debug_log
from bot.services.mtproto_bot_fallback import telethon_missed_command_message
from bot.services.mtproto_dm_gc_listener import _clients, get_dm_gc_listener_status
from bot.services.mtproto_group_cash import parse_cash_command
from cashier.services.group_cash_init import (
    WORKING_ON_CASHOUT_MESSAGE,
    initiate_group_cash_job,
)

logger = logging.getLogger(__name__)


def _can_use_cash(user_id: int, club_id: int) -> bool:
    if is_club_staff(user_id, club_id):
        return True
    if user_id in ADMIN_USER_IDS:
        return get_club_allows_admin_commands(club_id)
    return False


def _parse_from_args(args: list[str]) -> Decimal | None:
    if not args:
        return None
    return parse_cash_command("/cash " + args[0])


async def _cash_bot_api_path(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    club_id: int,
    amount: Decimal,
    command_already_deleted: bool = False,
) -> None:
    chat = update.effective_chat
    admin_id = update.effective_user.id
    assert chat is not None and update.message is not None

    if not command_already_deleted:
        try:
            await context.bot.delete_message(
                chat_id=chat.id, message_id=update.message.message_id
            )
        except Exception:
            logger.warning(
                "cash: could not delete command message chat_id=%s message_id=%s",
                chat.id,
                update.message.message_id,
                exc_info=True,
            )

    await update.message.reply_text(WORKING_ON_CASHOUT_MESSAGE)

    try:
        job_id = initiate_group_cash_job(
            chat_id=chat.id,
            club_id=club_id,
            group_title=chat.title or "Unknown group",
            amount=amount,
            initiated_by=admin_id,
        )
        logger.info(
            "cash: wizard started job_id=%s chat_id=%s club_id=%s amount=%s admin=%s",
            job_id,
            chat.id,
            club_id,
            amount,
            admin_id,
        )
    except Exception:
        logger.exception(
            "cash: initiate_group_cash_job failed club_id=%s chat_id=%s",
            club_id,
            chat.id,
        )
        await context.bot.send_message(
            chat_id=chat.id,
            text="Could not start cashout wizard. Try again or contact support.",
        )


async def _cash_telethon_fallback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    club_id: int,
    amount: Decimal,
) -> None:
    chat = update.effective_chat
    assert chat is not None and update.message is not None

    missed = await telethon_missed_command_message(
        context.bot,
        chat_id=chat.id,
        message_id=update.message.message_id,
        command="/cash",
    )
    if not missed:
        return

    await _cash_bot_api_path(
        update,
        context,
        club_id=club_id,
        amount=amount,
        command_already_deleted=True,
    )


async def cash_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    if not _can_use_cash(admin_id, club_id):
        return

    amount = _parse_from_args(context.args or [])
    if amount is None:
        await update.message.reply_text(
            "Usage: /cash <amount> (Example: /cash 500)"
        )
        return

    if get_club_config_for_admin(admin_id) and is_dm_gc_listener_enabled():
        # #region agent log
        _club_cfg = get_club_config_for_admin(admin_id)
        _conn = {
            getattr(c, "_gg_club_key", "?"): c.is_connected() for c in _clients
        }
        agent_debug_log(
            hypothesis_id="B",
            location="cash.py:cash_handler",
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
            _cash_telethon_fallback(
                update,
                context,
                club_id=club_id,
                amount=amount,
            ),
            name=f"cash-telethon-fallback-{chat.id}",
        )
        return

    await _cash_bot_api_path(
        update,
        context,
        club_id=club_id,
        amount=amount,
    )
