"""Bot API fallback when Telethon listener misses an outgoing group command."""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from bot.services.agent_debug_log import agent_debug_log

logger = logging.getLogger(__name__)

_FALLBACK_DELAY_SEC = 4.0


async def telethon_missed_command_message(
    bot,
    *,
    chat_id: int,
    message_id: int,
    command: str,
) -> bool:
    """Return True when the command message is still present (Telethon did not handle it)."""
    await asyncio.sleep(_FALLBACK_DELAY_SEC)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as exc:
        agent_debug_log(
            hypothesis_id="B",
            location="mtproto_bot_fallback.py:telethon_missed_command_message",
            message="mtproto_fallback_skip_telethon_likely_handled",
            data={
                "command": command,
                "chat_id": chat_id,
                "message_id": message_id,
                "delete_error": type(exc).__name__,
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
