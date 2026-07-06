"""MTProto /lmk in support groups: delete command and send reminder."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from telethon import events

from club_gc_settings import ClubGcConfig
from bot.services.club import get_club_for_chat
from bot.services.mtproto_bot_fallback import bot_delete_message

logger = logging.getLogger(__name__)

LMK_MESSAGE = "Let me know once its sent!!"

_LMK_CMD_RE = re.compile(r"^/lmk(?:@\w+)?\s*$", re.IGNORECASE)


def parse_lmk_command(text: str) -> bool:
    return bool(_LMK_CMD_RE.match((text or "").strip()))


async def _delete_lmk_command_message(
    event: events.NewMessage.Event,
    *,
    ptb_bot: Any | None,
    club_key: str,
    listener_label: str,
) -> None:
    deleted = False
    try:
        await event.delete()
        deleted = True
    except Exception as e:
        logger.warning(
            "group_lmk: telethon delete failed club=%s listener=%s chat_id=%s err=%s",
            club_key,
            listener_label,
            event.chat_id,
            type(e).__name__,
        )

    if deleted or ptb_bot is None or event.chat_id is None or not event.message:
        return

    await bot_delete_message(
        ptb_bot,
        chat_id=int(event.chat_id),
        message_id=int(event.message.id),
    )


async def handle_group_lmk_outgoing(
    event: events.NewMessage.Event,
    cfg: ClubGcConfig,
    *,
    listener_label: str,
    ptb_bot: Any | None = None,
) -> None:
    """Outgoing /lmk in a megagroup: delete command and post reminder."""
    if event.is_private:
        return

    if not parse_lmk_command(event.raw_text or ""):
        return

    club_id = await asyncio.to_thread(get_club_for_chat, event.chat_id)
    if club_id is None or int(club_id) != int(cfg.link_club_id):
        return

    await _delete_lmk_command_message(
        event,
        ptb_bot=ptb_bot,
        club_key=cfg.club_key,
        listener_label=listener_label,
    )

    try:
        await event.client.send_message(event.chat_id, LMK_MESSAGE)
    except Exception:
        logger.exception(
            "group_lmk: send failed club=%s chat_id=%s",
            cfg.club_key,
            event.chat_id,
        )
