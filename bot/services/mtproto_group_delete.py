"""MTProto ``/delete confirm`` in support groups: kick participants and delete the megagroup.

Outgoing-only (staff type on the club MTProto account). Requires a linked group for that club.
Does not remove Postgres rows (``groups``, ``player_details``, ``support_group_chats``, etc.).

Megagroups created via ``/gc`` are usually deletable because the MTProto account is the creator.
Older groups where that account is not creator/admin may fail ``DeleteChannelRequest``.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
from typing import Any

from telethon import events
from telethon.errors import RPCError
from telethon.errors.rpcerrorlist import (
    ChannelInvalidError,
    ChannelPrivateError,
    ChannelTooLargeError,
    ChatAdminRequiredError,
)
from telethon.tl.functions.channels import DeleteChannelRequest, EditBannedRequest
from telethon.tl.types import ChatBannedRights

from club_gc_settings import ClubGcConfig
from bot.services.club import get_club_for_chat
from bot.services.mtproto_group_create import _with_single_flood_retry, get_mtproto_lock

logger = logging.getLogger(__name__)

_DELETE_CONFIRM_RE = re.compile(r"^/delete(?:@\w+)?\s+confirm\s*$", re.IGNORECASE)

_KICK_BAN_RIGHTS = ChatBannedRights(
    until_date=datetime.timedelta(seconds=30),
    view_messages=True,
)


def parse_delete_confirm_command(text: str) -> bool:
    """True when text is exactly ``/delete confirm`` (optional @bot suffix)."""
    return bool(_DELETE_CONFIRM_RE.match((text or "").strip()))


async def _notify_delete_failure(
    client: Any,
    *,
    cfg: ClubGcConfig,
    chat_id: int,
    reason: str,
) -> None:
    admin_id = int(cfg.command_admin_user_id)
    text = (
        f"[{cfg.club_display_name}] /delete confirm failed (chat {chat_id}):\n"
        f"{reason}"
    )[:4096]
    try:
        await client.send_message(admin_id, text)
    except Exception:
        logger.warning(
            "group_delete: admin DM failed club=%s admin=%s chat_id=%s reason=%s",
            cfg.club_key,
            admin_id,
            chat_id,
            reason,
            exc_info=True,
        )


async def _kick_all_participants(client: Any, channel_ent: Any, self_id: int) -> tuple[int, int]:
    """Kick every participant except ``self_id``. Returns (kicked, failed)."""
    kicked = 0
    failed = 0

    async def walk():
        nonlocal kicked, failed
        async for user in client.iter_participants(channel_ent):
            uid = getattr(user, "id", None)
            if uid is None:
                continue
            uid = int(uid)
            if uid == self_id:
                continue

            async def do_kick(u=user):
                await client(
                    EditBannedRequest(channel_ent, u, _KICK_BAN_RIGHTS)
                )

            try:
                await _with_single_flood_retry(
                    f"kick_participant:{uid}",
                    do_kick,
                )
                kicked += 1
            except Exception as e:
                failed += 1
                logger.warning(
                    "group_delete: kick failed chat=%s user=%s err=%s",
                    getattr(channel_ent, "id", "?"),
                    uid,
                    type(e).__name__,
                )

    await _with_single_flood_retry("iter_participants_delete", walk)
    return kicked, failed


async def _delete_channel(client: Any, channel_ent: Any) -> None:
    await _with_single_flood_retry(
        "DeleteChannelRequest",
        lambda: client(DeleteChannelRequest(channel_ent)),
    )


def _rpc_error_reason(exc: RPCError) -> str:
    if isinstance(exc, ChannelPrivateError):
        return "Not in this group or no access (ChannelPrivate)."
    if isinstance(exc, ChatAdminRequiredError):
        return "MTProto account lacks admin rights (ChatAdminRequired)."
    if isinstance(exc, ChannelInvalidError):
        return "Invalid channel (ChannelInvalid)."
    if isinstance(exc, ChannelTooLargeError):
        return "Group too large to delete (ChannelTooLarge)."
    return f"{type(exc).__name__}: {exc}"


async def _erase_group_chat(
    client: Any,
    *,
    cfg: ClubGcConfig,
    chat_id: int,
    channel_ent: Any,
) -> str | None:
    """Kick participants then delete channel. Returns error reason or None on success."""
    try:
        me = await client.get_me()
        self_id = int(me.id) if me and getattr(me, "id", None) is not None else None
    except Exception as e:
        return f"Could not resolve MTProto self id ({type(e).__name__})."

    if self_id is None:
        return "Could not resolve MTProto self id."

    try:
        kicked, kick_failed = await _kick_all_participants(
            client, channel_ent, self_id
        )
        logger.info(
            "group_delete: kicks club=%s chat_id=%s kicked=%s failed=%s",
            cfg.club_key,
            chat_id,
            kicked,
            kick_failed,
        )
    except Exception as e:
        logger.warning(
            "group_delete: participant walk failed club=%s chat_id=%s err=%s",
            cfg.club_key,
            chat_id,
            type(e).__name__,
            exc_info=True,
        )
        return f"Could not list/kick participants ({type(e).__name__})."

    try:
        await _delete_channel(client, channel_ent)
        logger.info(
            "group_delete: channel deleted club=%s chat_id=%s",
            cfg.club_key,
            chat_id,
        )
        return None
    except RPCError as e:
        return _rpc_error_reason(e)
    except Exception as e:
        return f"DeleteChannel failed ({type(e).__name__})."


async def handle_group_delete_outgoing(
    event: events.NewMessage.Event,
    cfg: ClubGcConfig,
    *,
    listener_label: str,
) -> None:
    """Outgoing ``/delete confirm`` in a megagroup: delete command, kick all, delete channel."""
    if event.is_private:
        return

    if not parse_delete_confirm_command(event.raw_text or ""):
        return

    chat_id = int(event.chat_id) if event.chat_id is not None else None
    if chat_id is None:
        logger.warning(
            "group_delete: no chat_id club=%s listener=%s",
            cfg.club_key,
            listener_label,
        )
        return

    club_id = await asyncio.to_thread(get_club_for_chat, chat_id)
    if club_id is None:
        logger.warning(
            "group_delete: group not linked club=%s listener=%s chat_id=%s",
            cfg.club_key,
            listener_label,
            chat_id,
        )
        return
    if int(club_id) != int(cfg.link_club_id):
        logger.warning(
            "group_delete: club_id mismatch club=%s listener=%s chat_id=%s "
            "group_club_id=%s expected=%s",
            cfg.club_key,
            listener_label,
            chat_id,
            club_id,
            cfg.link_club_id,
        )
        return

    try:
        await event.delete()
    except Exception as e:
        logger.warning(
            "group_delete: delete command failed club=%s chat_id=%s err=%s",
            cfg.club_key,
            chat_id,
            type(e).__name__,
        )

    async with get_mtproto_lock(cfg.club_key):
        client = event.client
        try:
            channel_ent = await event.get_chat()
        except Exception as e:
            reason = f"Could not open chat ({type(e).__name__})."
            logger.warning(
                "group_delete: get_chat failed club=%s chat_id=%s err=%s",
                cfg.club_key,
                chat_id,
                type(e).__name__,
            )
            await _notify_delete_failure(client, cfg=cfg, chat_id=chat_id, reason=reason)
            return

        err = await _erase_group_chat(
            client,
            cfg=cfg,
            chat_id=chat_id,
            channel_ent=channel_ent,
        )
        if err:
            await _notify_delete_failure(client, cfg=cfg, chat_id=chat_id, reason=err)
