"""MTProto /cash in support groups: working message + GGCashier job (defer pin/ASAP)."""

from __future__ import annotations

import asyncio
import logging
import re
from decimal import Decimal
from typing import Any

from telethon import events

from club_gc_settings import ClubGcConfig, get_club_gc_config_by_link_club_id
from bot.services.club import get_club_for_chat
from bot.services.mtproto_group_add import _format_money, _parse_money_token
from bot.services.mtproto_group_create import (
    get_mtproto_lock,
    is_client_authorized,
    make_client,
)
from cashier.services.group_cash_init import (
    WORKING_ON_CASHOUT_MESSAGE,
    initiate_group_cash_job,
)

logger = logging.getLogger(__name__)

CASH_ASAP_MESSAGE = "Your cashout will be processed ASAP!"

_CASH_CMD_RE = re.compile(r"^/cash(?:@\w+)?\s+(\S+)\s*$", re.IGNORECASE)


def _cash_notify_bot() -> Any | None:
    from bot.services.mtproto_dm_gc_listener import _loop_holder

    return _loop_holder.get("ptb_bot")


async def _notify_invoker_cash_failure(
    *,
    invoker_user_id: int | None,
    cfg: ClubGcConfig,
    chat_id: int,
    reason: str,
    ptb_bot: Any | None = None,
) -> None:
    """DM the staff member who sent /cash when Telethon did not complete processing."""
    if not invoker_user_id:
        logger.warning(
            "group_cash: cannot DM invoker (no sender_id) chat_id=%s reason=%s",
            chat_id,
            reason,
        )
        return

    bot = ptb_bot or _cash_notify_bot()
    if not bot:
        logger.warning(
            "group_cash: cannot DM invoker user_id=%s chat_id=%s (bot unavailable): %s",
            invoker_user_id,
            chat_id,
            reason,
        )
        return

    text = (
        f"[{cfg.club_display_name}] /cash was not processed (chat {chat_id}):\n"
        f"{reason}"
    )[:4096]
    try:
        await bot.send_message(chat_id=int(invoker_user_id), text=text)
    except Exception:
        logger.warning(
            "group_cash: DM notify failed invoker=%s chat_id=%s reason=%s",
            invoker_user_id,
            chat_id,
            reason,
            exc_info=True,
        )


def parse_cash_command(text: str) -> Decimal | None:
    m = _CASH_CMD_RE.match((text or "").strip())
    if not m:
        return None
    return _parse_money_token(m.group(1))


def format_cash_owed(amount: Decimal) -> str:
    return f"{_format_money(amount)} owed"


async def _execute_cash_flow(cfg: ClubGcConfig, chat_id: int, amount: Decimal) -> None:
    """Pin owed amount and send ASAP (called after wizard completes)."""
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
    ptb_bot: Any | None = None,
) -> None:
    """Outgoing /cash: delete command, post working message, start GGCashier job."""
    if event.is_private:
        return

    amount = parse_cash_command(event.raw_text or "")
    if amount is None:
        return

    invoker_user_id = event.sender_id
    chat_id = int(event.chat_id) if event.chat_id is not None else None
    if chat_id is None:
        await _notify_invoker_cash_failure(
            invoker_user_id=invoker_user_id,
            cfg=cfg,
            chat_id=0,
            reason="Telethon event has no chat_id — cannot resolve this group.",
            ptb_bot=ptb_bot,
        )
        return

    club_id = await asyncio.to_thread(get_club_for_chat, chat_id)
    if club_id is None:
        logger.warning(
            "group_cash: group not linked club=%s listener=%s chat_id=%s",
            cfg.club_key,
            listener_label,
            chat_id,
        )
        await _notify_invoker_cash_failure(
            invoker_user_id=invoker_user_id,
            cfg=cfg,
            chat_id=chat_id,
            reason=(
                "This group is not linked to a club "
                f"(get_club_for_chat returned None for chat_id={chat_id})."
            ),
            ptb_bot=ptb_bot,
        )
        return
    if int(club_id) != int(cfg.link_club_id):
        logger.warning(
            "group_cash: club_id mismatch club=%s listener=%s chat_id=%s "
            "group_club_id=%s expected_link_club_id=%s",
            cfg.club_key,
            listener_label,
            chat_id,
            club_id,
            cfg.link_club_id,
        )
        await _notify_invoker_cash_failure(
            invoker_user_id=invoker_user_id,
            cfg=cfg,
            chat_id=chat_id,
            reason=(
                f"Group is linked to club_id={club_id} but "
                f"{cfg.club_display_name} MTProto expects link_club_id="
                f"{cfg.link_club_id}."
            ),
            ptb_bot=ptb_bot,
        )
        return

    try:
        await event.delete()
    except Exception as e:
        logger.warning(
            "group_cash: delete failed club=%s listener=%s chat_id=%s err=%s",
            cfg.club_key,
            listener_label,
            chat_id,
            type(e).__name__,
        )

    group_title = "Unknown group"
    try:
        chat = await event.get_chat()
        group_title = getattr(chat, "title", None) or group_title
        await event.client.send_message(chat_id, WORKING_ON_CASHOUT_MESSAGE)
    except Exception as e:
        logger.exception(
            "group_cash: working message failed club=%s chat_id=%s",
            cfg.club_key,
            chat_id,
        )
        await _notify_invoker_cash_failure(
            invoker_user_id=invoker_user_id,
            cfg=cfg,
            chat_id=chat_id,
            reason=f"Failed to post “{WORKING_ON_CASHOUT_MESSAGE}”: {type(e).__name__}.",
            ptb_bot=ptb_bot,
        )
        return

    if not invoker_user_id:
        logger.warning("group_cash: no sender_id chat_id=%s", chat_id)
        await _notify_invoker_cash_failure(
            invoker_user_id=None,
            cfg=cfg,
            chat_id=chat_id,
            reason="Could not determine who sent /cash (sender_id missing).",
            ptb_bot=ptb_bot,
        )
        return

    try:
        job_id = await asyncio.to_thread(
            initiate_group_cash_job,
            chat_id=chat_id,
            club_id=int(club_id),
            group_title=group_title,
            amount=amount,
            initiated_by=int(invoker_user_id),
        )
        logger.info(
            "group_cash: wizard started job_id=%s chat_id=%s club_id=%s amount=%s staff=%s",
            job_id,
            chat_id,
            club_id,
            amount,
            invoker_user_id,
        )
    except Exception as e:
        logger.exception(
            "group_cash: initiate_group_cash_job failed club_id=%s chat_id=%s",
            club_id,
            chat_id,
        )
        await _notify_invoker_cash_failure(
            invoker_user_id=invoker_user_id,
            cfg=cfg,
            chat_id=chat_id,
            reason=f"Failed to start GGCashier job: {type(e).__name__}.",
            ptb_bot=ptb_bot,
        )
