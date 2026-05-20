"""MTProto /cash in support groups: delete command, pin owed amount, send ASAP message."""

from __future__ import annotations

import asyncio
import logging
import re
from decimal import Decimal

from telethon import events

from club_gc_settings import ClubGcConfig, get_club_gc_config_by_link_club_id
from bot.services.club import (
    get_club_for_chat,
    invalidate_pending_one_time_bypasses,
    record_activity_for_chat,
)
from bot.services.mtproto_group_add import _format_money, _parse_money_token
from bot.services.mtproto_group_create import (
    get_mtproto_lock,
    is_client_authorized,
    make_client,
)

logger = logging.getLogger(__name__)

CASH_ASAP_MESSAGE = "Getting this sent ASAP!!!!"

_CASH_CMD_RE = re.compile(r"^/cash(?:@\w+)?\s+(\S+)\s*$", re.IGNORECASE)


def parse_cash_command(text: str) -> Decimal | None:
    m = _CASH_CMD_RE.match((text or "").strip())
    if not m:
        return None
    return _parse_money_token(m.group(1))


def format_cash_owed(amount: Decimal) -> str:
    return f"{_format_money(amount)} owed"


async def _execute_cash_flow(cfg: ClubGcConfig, chat_id: int, amount: Decimal) -> None:
    owed_text = format_cash_owed(amount)
    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg)
        await client.connect()
        try:
            if not await is_client_authorized(cfg):
                logger.warning(
                    "group_cash: MTProto not authorized club=%s", cfg.club_key
                )
                return
            owed_msg = await client.send_message(chat_id, owed_text)
            try:
                await owed_msg.pin(notify=False)
            except Exception as e:
                logger.warning(
                    "group_cash: pin failed club=%s chat_id=%s err=%s",
                    cfg.club_key,
                    chat_id,
                    type(e).__name__,
                )
            await client.send_message(chat_id, CASH_ASAP_MESSAGE)
        finally:
            await client.disconnect()


def schedule_cash_flow_from_club(
    *,
    chat_id: int,
    club_id: int,
    amount: Decimal,
) -> None:
    """Run cash flow from the club MTProto user (not the bot)."""
    cfg = get_club_gc_config_by_link_club_id(int(club_id))
    if not cfg:
        logger.warning("group_cash: no ClubGcConfig for club_id=%s", club_id)
        return

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("group_cash: no running event loop chat_id=%s", chat_id)
        return

    from bot.services.mtproto_dm_gc_listener import _loop_holder

    mtproto_loop = _loop_holder.get("loop")
    coro = _execute_cash_flow(cfg, chat_id, amount)
    if mtproto_loop and mtproto_loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, mtproto_loop)
    else:
        asyncio.create_task(coro, name=f"group-cash-{chat_id}")


async def handle_group_cash_outgoing(
    event: events.NewMessage.Event,
    cfg: ClubGcConfig,
    *,
    listener_label: str,
) -> None:
    """Outgoing /cash in a megagroup: delete command, pin owed, send ASAP, record cooldown."""
    if event.is_private:
        return

    amount = parse_cash_command(event.raw_text or "")
    if amount is None:
        return

    club_id = await asyncio.to_thread(get_club_for_chat, event.chat_id)
    if club_id is None or int(club_id) != int(cfg.link_club_id):
        return

    try:
        await event.delete()
    except Exception as e:
        logger.warning(
            "group_cash: delete failed club=%s listener=%s chat_id=%s err=%s",
            cfg.club_key,
            listener_label,
            event.chat_id,
            type(e).__name__,
        )

    owed_text = format_cash_owed(amount)
    try:
        owed_msg = await event.client.send_message(event.chat_id, owed_text)
        try:
            await owed_msg.pin(notify=False)
        except Exception as e:
            logger.warning(
                "group_cash: pin failed club=%s listener=%s chat_id=%s err=%s",
                cfg.club_key,
                listener_label,
                event.chat_id,
                type(e).__name__,
            )
        await event.client.send_message(event.chat_id, CASH_ASAP_MESSAGE)
    except Exception:
        logger.exception(
            "group_cash: send failed club=%s chat_id=%s",
            cfg.club_key,
            event.chat_id,
        )
        return

    try:
        await asyncio.to_thread(
            record_activity_for_chat,
            int(club_id),
            int(event.chat_id),
            "cashout",
        )
        await asyncio.to_thread(
            invalidate_pending_one_time_bypasses,
            int(club_id),
            int(event.chat_id),
        )
    except Exception:
        logger.exception(
            "group_cash: record_activity failed club_id=%s chat_id=%s",
            club_id,
            event.chat_id,
        )
