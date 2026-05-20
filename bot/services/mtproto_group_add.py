"""MTProto /add in support groups: delete command and send confirmation (like /gc)."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from decimal import Decimal, InvalidOperation

from telethon import events

from club_gc_settings import ClubGcConfig, get_club_gc_config_by_link_club_id
from bot.services.club import (
    get_club_for_chat,
    invalidate_pending_one_time_bypasses,
    record_activity_for_chat,
)
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

_ADD_CMD_RE = re.compile(r"^/add(?:@\w+)?\s+(.+?)\s*$", re.IGNORECASE)


def _parse_money_token(raw: str) -> Decimal | None:
    cleaned = raw.strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(cleaned)
        if amount <= 0:
            return None
        return amount
    except (InvalidOperation, Exception):
        return None


def _format_money(amount: Decimal) -> str:
    """Whole dollars only, no decimal suffix (e.g. $500, $1,000)."""
    return f"${int(amount):,}"


def parse_add_command(
    text: str,
) -> tuple[Decimal, Decimal | None, str | None] | None:
    """Return (amount, optional_bonus, optional_name).

    Examples:
        /add 500
        /add 500 50
        /add 500 Jacob
        /add 500 50 Jacob
    """
    m = _ADD_CMD_RE.match((text or "").strip())
    if not m:
        return None
    parts = m.group(1).split()
    if not parts:
        return None
    amount = _parse_money_token(parts[0])
    if amount is None:
        return None
    if len(parts) == 1:
        return amount, None, None
    second_money = _parse_money_token(parts[1])
    if second_money is not None:
        bonus = second_money
        name = " ".join(parts[2:]).strip() or None
        return amount, bonus, name
    name = " ".join(parts[1:]).strip() or None
    return amount, None, name


def parse_add_amount(text: str) -> Decimal | None:
    """Backward-compatible: amount only."""
    parsed = parse_add_command(text)
    if parsed is None:
        return None
    return parsed[0]


def format_add_confirmation(
    amount: Decimal,
    bonus: Decimal | None = None,
    *,
    name: str | None = None,
) -> str:
    phrase = random.choice(ADD_CONFIRMATION_MESSAGES)
    amt = _format_money(amount)
    if bonus is not None:
        core = f"Added {amt} plus {_format_money(bonus)} bonus, {phrase}"
    else:
        core = f"Added {amt}, {phrase}"
    if name:
        return f"{core} {name}!!"
    return f"{core}!!"


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
    """Outgoing /add in a megagroup: delete command, send new message, record cooldown."""
    if event.is_private:
        return

    parsed = parse_add_command(event.raw_text or "")
    if parsed is None:
        return
    amount, bonus, name = parsed

    club_id = await asyncio.to_thread(get_club_for_chat, event.chat_id)
    if club_id is None or int(club_id) != int(cfg.link_club_id):
        return

    confirmation = format_add_confirmation(amount, bonus, name=name)

    try:
        await event.delete()
    except Exception as e:
        logger.warning(
            "group_add: delete failed club=%s listener=%s chat_id=%s err=%s",
            cfg.club_key,
            listener_label,
            event.chat_id,
            type(e).__name__,
        )

    try:
        await event.client.send_message(event.chat_id, confirmation)
    except Exception:
        logger.exception(
            "group_add: send failed club=%s chat_id=%s",
            cfg.club_key,
            event.chat_id,
        )
        return

    try:
        await asyncio.to_thread(
            record_activity_for_chat,
            int(club_id),
            int(event.chat_id),
            "deposit",
        )
        await asyncio.to_thread(
            invalidate_pending_one_time_bypasses,
            int(club_id),
            int(event.chat_id),
        )
    except Exception:
        logger.exception(
            "group_add: record_activity failed club_id=%s chat_id=%s",
            club_id,
            event.chat_id,
        )
