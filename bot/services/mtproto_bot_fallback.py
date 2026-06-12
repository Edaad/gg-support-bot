"""Bot API fallback when Telethon listener misses an outgoing group command."""

from __future__ import annotations

import asyncio
import logging

from bot.services.agent_debug_log import agent_debug_log

logger = logging.getLogger(__name__)

_FALLBACK_DELAY_SEC = 4.0


def message_delete_not_found(exc: Exception) -> bool:
    """True when Telegram reports the message is already gone."""
    return "message to delete not found" in str(exc).lower()


async def bot_delete_message(bot, *, chat_id: int, message_id: int) -> bool:
    """Delete via Bot API. Return True when deleted, False when already gone or failed."""
    try:
        await bot.delete_message(chat_id=int(chat_id), message_id=int(message_id))
        return True
    except Exception as exc:
        if message_delete_not_found(exc):
            return False
        logger.warning(
            "bot_delete_message failed chat_id=%s message_id=%s err=%s",
            chat_id,
            message_id,
            type(exc).__name__,
        )
        return False


async def telethon_missed_command_message(
    bot,
    *,
    chat_id: int,
    message_id: int,
    command: str,
) -> bool:
    """Return True when the command message is still present (Telethon did not handle it)."""
    await asyncio.sleep(_FALLBACK_DELAY_SEC)
    deleted = await bot_delete_message(bot, chat_id=chat_id, message_id=message_id)
    if not deleted:
        agent_debug_log(
            hypothesis_id="B",
            location="mtproto_bot_fallback.py:telethon_missed_command_message",
            message="mtproto_fallback_skip_message_gone",
            data={
                "command": command,
                "chat_id": chat_id,
                "message_id": message_id,
            },
        )
        return False

    agent_debug_log(
        hypothesis_id="B",
        location="mtproto_bot_fallback.py:telethon_missed_command_message",
        message="mtproto_fallback_triggered",
        data={
            "command": command,
            "chat_id": chat_id,
            "message_id": message_id,
        },
    )
    logger.warning(
        "mtproto_fallback: Telethon missed %s chat_id=%s message_id=%s — using Bot API",
        command,
        chat_id,
        message_id,
    )
    return True
