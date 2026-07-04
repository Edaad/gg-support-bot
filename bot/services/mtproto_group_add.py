"""MTProto /add in support groups: delete command and send confirmation (like /gc)."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from telethon import events

from club_gc_settings import ClubGcConfig, get_club_gc_config_by_link_club_id
from bot.services.club import (
    get_club_for_chat,
    invalidate_pending_one_time_bypasses,
    record_activity_for_chat,
)
from bot.services.agent_debug_log import agent_debug_log
from bot.services.mtproto_bot_fallback import bot_delete_message
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
_ADD_SHORTHAND_RE = re.compile(r"^/(\d+)(?:@\w+)?(?:\s+(.+?))?\s*$", re.IGNORECASE)


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
        /500
        /500 50 Jacob
    """
    raw = (text or "").strip()
    m = _ADD_CMD_RE.match(raw)
    if not m:
        sm = _ADD_SHORTHAND_RE.match(raw)
        if not sm:
            return None
        rest = (sm.group(2) or "").strip()
        raw = f"/add {sm.group(1)}" + (f" {rest}" if rest else "")
        m = _ADD_CMD_RE.match(raw)
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


def _format_chips(amount: Decimal) -> str:
    """Whole chips count (e.g. 500 chips, 1,000 chips)."""
    return f"{int(amount):,} chips"


def format_add_confirmation(
    amount: Decimal,
    bonus: Decimal | None = None,
    *,
    name: str | None = None,
) -> str:
    phrase = random.choice(ADD_CONFIRMATION_MESSAGES)
    amt = _format_chips(amount)
    if bonus is not None:
        core = f"Added {amt} plus {int(bonus):,} bonus, {phrase}"
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


async def _delete_add_command_message(
    event: events.NewMessage.Event,
    *,
    ptb_bot: Any | None,
    club_key: str,
    listener_label: str,
) -> None:
    """Delete /add command via Telethon, then Bot API if needed."""
    deleted = False
    try:
        await event.delete()
        deleted = True
    except Exception as e:
        logger.warning(
            "group_add: telethon delete failed club=%s listener=%s chat_id=%s err=%s",
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


async def handle_group_add_outgoing(
    event: events.NewMessage.Event,
    cfg: ClubGcConfig,
    *,
    listener_label: str,
    ptb_bot: Any | None = None,
) -> None:
    """Outgoing /add in a megagroup: delete command, send new message, record cooldown."""
    _raw = (event.raw_text or "")[:120]
    _stripped = _raw.lstrip().lower()
    _is_add_candidate = _stripped.startswith("/add") or (
        _stripped.startswith("/") and len(_stripped) > 1 and _stripped[1].isdigit()
    )
    if _is_add_candidate:
        # #region agent log
        agent_debug_log(
            hypothesis_id="C",
            location="mtproto_group_add.py:handle_group_add_outgoing:entry",
            message="telethon_outgoing_add_candidate",
            data={
                "club_key": cfg.club_key,
                "listener_label": listener_label,
                "chat_id": event.chat_id,
                "is_private": event.is_private,
                "client_connected": event.client.is_connected(),
                "raw_text": _raw,
            },
        )
        # #endregion
    if event.is_private:
        return

    parsed = parse_add_command(event.raw_text or "")
    if parsed is None:
        return
    amount, bonus, name = parsed

    club_id = await asyncio.to_thread(get_club_for_chat, event.chat_id)
    if club_id is None or int(club_id) != int(cfg.link_club_id):
        # #region agent log
        agent_debug_log(
            hypothesis_id="D",
            location="mtproto_group_add.py:handle_group_add_outgoing:club_mismatch",
            message="telethon_add_filtered_club_id_mismatch",
            data={
                "club_key": cfg.club_key,
                "event_chat_id": event.chat_id,
                "resolved_club_id": club_id,
                "cfg_link_club_id": cfg.link_club_id,
            },
        )
        # #endregion
        return

    confirmation = format_add_confirmation(amount, bonus, name=name)

    await _delete_add_command_message(
        event,
        ptb_bot=ptb_bot,
        club_key=cfg.club_key,
        listener_label=listener_label,
    )

    try:
        await event.client.send_message(event.chat_id, confirmation)
        # #region agent log
        agent_debug_log(
            hypothesis_id="C",
            location="mtproto_group_add.py:handle_group_add_outgoing:success",
            message="telethon_add_confirmation_sent",
            data={
                "club_key": cfg.club_key,
                "chat_id": event.chat_id,
                "amount": str(amount),
            },
        )
        # #endregion
    except Exception:
        # #region agent log
        agent_debug_log(
            hypothesis_id="C",
            location="mtproto_group_add.py:handle_group_add_outgoing:send_failed",
            message="telethon_add_send_failed",
            data={
                "club_key": cfg.club_key,
                "chat_id": event.chat_id,
                "client_connected": event.client.is_connected(),
            },
        )
        # #endregion
        logger.exception(
            "group_add: send failed club=%s chat_id=%s",
            cfg.club_key,
            event.chat_id,
        )
        return

    from bot.handlers.deposit import cancel_deposit_reminder_for_chat

    cancel_deposit_reminder_for_chat(int(event.chat_id))

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

    # Optional ClubGG auto chip-adding (no-op unless enabled + configured).
    try:
        from bot.services.clubgg_deposit_api import trigger_auto_chip_add

        message_id = event.message.id if event.message else None
        if message_id is not None:
            asyncio.create_task(
                trigger_auto_chip_add(
                    club_id=int(club_id),
                    chat_id=int(event.chat_id),
                    message_id=int(message_id),
                    amount=amount,
                    bonus=bonus,
                    group_title=None,
                    ptb_bot=ptb_bot,
                )
            )
    except Exception:
        logger.exception(
            "group_add: failed to schedule auto chip-add club_id=%s chat_id=%s",
            club_id,
            event.chat_id,
        )

    if bonus is not None and ptb_bot is not None:
        invoker_user_id = event.sender_id
        if invoker_user_id is None:
            logger.warning(
                "group_add: no sender_id for bonus draft chat_id=%s",
                event.chat_id,
            )
        else:
            from bot.services.bonus_from_add import maybe_start_bonus_recording_from_add

            group_title = "Unknown group"
            try:
                chat = await event.get_chat()
                group_title = getattr(chat, "title", None) or group_title
            except Exception:
                pass
            asyncio.create_task(
                maybe_start_bonus_recording_from_add(
                    ptb_bot,
                    staff_user_id=int(invoker_user_id),
                    club_id=int(club_id),
                    chat_id=int(event.chat_id),
                    group_title=group_title,
                    bonus_amount=bonus,
                ),
                name=f"bonus-from-add-{event.chat_id}",
            )
