"""MTProto /add in support groups: edit outgoing command in place (like /gc delete)."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from decimal import Decimal, InvalidOperation

from telethon import events

from club_gc_settings import ClubGcConfig, get_club_gc_config_by_link_club_id
from bot.services.club import get_club_for_chat, record_activity
from bot.services.mtproto_group_create import (
    get_mtproto_lock,
    is_client_authorized,
    make_client,
)

logger = logging.getLogger(__name__)

ADD_CONFIRMATION_MESSAGES = (
    "good luck",
    "best of luck",
    "have fun at the tables",
    "best of luck at the tables",
    "enjoy",
)

_ADD_CMD_RE = re.compile(r"^/add(?:@\w+)?\s+(\S+)\s*$", re.IGNORECASE)


def parse_add_amount(text: str) -> Decimal | None:
    m = _ADD_CMD_RE.match((text or "").strip())
    if not m:
        return None
    raw = m.group(1).strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(raw)
        if amount <= 0:
            return None
        return amount
    except (InvalidOperation, Exception):
        return None


def format_add_confirmation(amount: Decimal) -> str:
    phrase = random.choice(ADD_CONFIRMATION_MESSAGES)
    return f"Added ${amount:,.2f}, {phrase}!!"


async def _send_add_confirmation_once(cfg: ClubGcConfig, chat_id: int, text: str) -> None:
    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg)
        await client.connect()
        try:
            if not await is_client_authorized(cfg):
                logger.warning(
                    "group_add: MTProto not authorized club=%s", cfg.club_key
                )
                return
            await client.send_message(chat_id, text)
        finally:
            await client.disconnect()


def schedule_send_add_confirmation_from_club(
    *,
    chat_id: int,
    club_id: int,
    text: str,
) -> None:
    """Send confirmation from the club MTProto user (not the bot)."""
    cfg = get_club_gc_config_by_link_club_id(int(club_id))
    if not cfg:
        logger.warning("group_add: no ClubGcConfig for club_id=%s", club_id)
        return

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("group_add: no running event loop chat_id=%s", chat_id)
        return

    from bot.services.mtproto_dm_gc_listener import _loop_holder

    mtproto_loop = _loop_holder.get("loop")
    coro = _send_add_confirmation_once(cfg, chat_id, text)
    if mtproto_loop and mtproto_loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, mtproto_loop)
    else:
        asyncio.create_task(coro, name=f"group-add-send-{chat_id}")


async def handle_group_add_outgoing(
    event: events.NewMessage.Event,
    cfg: ClubGcConfig,
    *,
    listener_label: str,
) -> None:
    """Outgoing /add in a megagroup: replace command text in place, record cooldown."""
    if event.is_private:
        return

    amount = parse_add_amount(event.raw_text or "")
    if amount is None:
        return

    club_id = await asyncio.to_thread(get_club_for_chat, event.chat_id)
    if club_id is None or int(club_id) != int(cfg.link_club_id):
        return

    reply = await event.get_reply_message()
    if not reply:
        return

    sender = await reply.get_sender()
    if not sender or getattr(sender, "bot", False):
        return

    confirmation = format_add_confirmation(amount)

    try:
        await event.edit(confirmation)
    except Exception as e:
        logger.warning(
            "group_add: edit failed club=%s listener=%s chat_id=%s err=%s",
            cfg.club_key,
            listener_label,
            event.chat_id,
            type(e).__name__,
        )
        try:
            await event.delete()
        except Exception:
            pass
        try:
            await event.client.send_message(event.chat_id, confirmation)
        except Exception:
            logger.exception(
                "group_add: send fallback failed club=%s chat_id=%s",
                cfg.club_key,
                event.chat_id,
            )
            return

    try:
        await asyncio.to_thread(
            record_activity,
            int(club_id),
            int(sender.id),
            int(event.chat_id),
            "deposit",
        )
    except Exception:
        logger.exception(
            "group_add: record_activity failed club_id=%s user_id=%s chat_id=%s",
            club_id,
            sender.id,
            event.chat_id,
        )
