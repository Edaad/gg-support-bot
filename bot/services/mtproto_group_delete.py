"""MTProto ``/delete confirm`` in support groups: kick participants and delete the group.

Outgoing-only (staff type on the club MTProto account). Requires a linked group for that club.
Does not remove Postgres rows (``groups``, ``player_details``, ``support_group_chats``, etc.).

Basic groups (``/gc`` since 2026-06) and legacy megagroups are both supported. The MTProto
account should be the creator; older groups where that account lacks admin may still fail.
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
from telethon.tl.functions.messages import DeleteChatRequest, DeleteChatUserRequest
from telethon.tl.types import Channel, Chat, ChatBannedRights
from telethon.utils import get_input_channel

from club_gc_settings import ClubGcConfig
from bot.services.club import get_club_for_chat
from bot.services.mtproto_group_create import _with_single_flood_retry, get_mtproto_lock
from bot.services.support_group_chats import fetch_support_group_chat_row_for_chat
from notification.chat_id import telegram_chat_id_variants

logger = logging.getLogger(__name__)

_DELETE_CONFIRM_RE = re.compile(r"^/delete(?:@\w+)?\s+confirm\s*$", re.IGNORECASE)

def _kick_ban_rights() -> ChatBannedRights:
    until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=30)
    return ChatBannedRights(until_date=until, view_messages=True)


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


async def _resolve_group_entity(client: Any, chat_id: int) -> Channel | Chat:
    """Full entity with access_hash; tries equivalent Bot API chat id forms."""
    last_exc: Exception | None = None
    for cid in sorted(telegram_chat_id_variants(int(chat_id)), key=lambda x: str(x)):
        try:
            entity = await client.get_entity(int(cid))
            if isinstance(entity, (Channel, Chat)):
                return entity
            raise TypeError(f"Cannot delete entity type {type(entity).__name__}")
        except Exception as exc:
            last_exc = exc
    detail = type(last_exc).__name__ if last_exc else "unknown"
    raise RuntimeError(f"Could not resolve chat_id {chat_id} ({detail})")


def _resolve_club_id_for_delete(chat_id: int, cfg: ClubGcConfig) -> int | None:
    """Return dashboard club id when this chat belongs to ``cfg``, else None."""
    for cid in telegram_chat_id_variants(int(chat_id)):
        club_id = get_club_for_chat(int(cid))
        if club_id is not None:
            return int(club_id) if int(club_id) == int(cfg.link_club_id) else None

    row = fetch_support_group_chat_row_for_chat(int(chat_id), club_key=cfg.club_key)
    if row is not None and row.club_key == cfg.club_key:
        return int(cfg.link_club_id)
    return None


async def _kick_all_participants(
    client: Any, entity: Channel | Chat, self_id: int
) -> tuple[int, int]:
    """Kick every participant except ``self_id``. Returns (kicked, failed)."""
    if isinstance(entity, Chat):
        return await _kick_all_basic_chat_participants(client, entity, self_id)

    kicked = 0
    failed = 0
    channel_inp = get_input_channel(entity)
    ban_rights = _kick_ban_rights()

    async def walk():
        nonlocal kicked, failed
        async for user in client.iter_participants(entity):
            uid = getattr(user, "id", None)
            if uid is None:
                continue
            uid = int(uid)
            if uid == self_id:
                continue

            async def do_kick(u=user):
                await client(EditBannedRequest(channel_inp, u, ban_rights))

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
                    getattr(entity, "id", "?"),
                    uid,
                    type(e).__name__,
                )

    await _with_single_flood_retry("iter_participants_delete", walk)
    return kicked, failed


async def _kick_all_basic_chat_participants(
    client: Any, entity: Chat, self_id: int
) -> tuple[int, int]:
    """Remove every member from a legacy basic group except ``self_id``."""
    kicked = 0
    failed = 0
    chat_id = int(entity.id)

    async def walk():
        nonlocal kicked, failed
        async for user in client.iter_participants(entity):
            uid = getattr(user, "id", None)
            if uid is None:
                continue
            uid = int(uid)
            if uid == self_id:
                continue

            async def do_kick(u=user):
                await client(
                    DeleteChatUserRequest(
                        chat_id=chat_id,
                        user_id=u,
                        revoke_history=True,
                    )
                )

            try:
                await _with_single_flood_retry(
                    f"kick_basic_participant:{uid}",
                    do_kick,
                )
                kicked += 1
            except Exception as e:
                failed += 1
                logger.warning(
                    "group_delete: basic kick failed chat=%s user=%s err=%s",
                    chat_id,
                    uid,
                    type(e).__name__,
                )

    await _with_single_flood_retry("iter_participants_delete_basic", walk)
    return kicked, failed


async def _delete_group_entity(client: Any, entity: Channel | Chat) -> None:
    if isinstance(entity, Channel):
        channel_inp = get_input_channel(entity)

        async def _delete_mega():
            await client(DeleteChannelRequest(channel_inp))

        await _with_single_flood_retry("DeleteChannelRequest", _delete_mega)
        return

    if isinstance(entity, Chat):

        async def _delete_basic():
            await client(DeleteChatRequest(chat_id=int(entity.id)))

        await _with_single_flood_retry("DeleteChatRequest", _delete_basic)
        return

    raise TypeError(f"Cannot delete entity type {type(entity).__name__}")


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
) -> str | None:
    """Kick participants then delete channel. Returns error reason or None on success."""
    try:
        entity = await _resolve_group_entity(client, chat_id)
    except Exception as e:
        return f"Could not resolve chat entity ({type(e).__name__}: {e})."

    try:
        me = await client.get_me()
        self_id = int(me.id) if me and getattr(me, "id", None) is not None else None
    except Exception as e:
        return f"Could not resolve MTProto self id ({type(e).__name__})."

    if self_id is None:
        return "Could not resolve MTProto self id."

    try:
        kicked, kick_failed = await _kick_all_participants(
            client, entity, self_id
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
        await _delete_group_entity(client, entity)
        logger.info(
            "group_delete: group deleted club=%s chat_id=%s entity=%s",
            cfg.club_key,
            chat_id,
            type(entity).__name__,
        )
        return None
    except RPCError as e:
        return _rpc_error_reason(e)
    except TypeError as e:
        return f"Delete failed ({e})."
    except Exception as e:
        return f"Delete failed ({type(e).__name__}: {e})."


async def erase_group_chat(
    client: Any,
    *,
    cfg: ClubGcConfig,
    chat_id: int,
) -> str | None:
    """Kick participants then delete channel. Returns error reason or None on success."""

    return await _erase_group_chat(client, cfg=cfg, chat_id=chat_id)


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

    club_id = await asyncio.to_thread(_resolve_club_id_for_delete, chat_id, cfg)
    if club_id is None:
        reason = (
            f"This group is not linked to {cfg.club_display_name} "
            f"(chat_id={chat_id}). Run /gc or /bind first, or check groups table."
        )
        logger.warning(
            "group_delete: group not linked club=%s listener=%s chat_id=%s",
            cfg.club_key,
            listener_label,
            chat_id,
        )
        await _notify_delete_failure(
            event.client, cfg=cfg, chat_id=chat_id, reason=reason
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
        err = await erase_group_chat(
            client,
            cfg=cfg,
            chat_id=chat_id,
        )
        if err:
            await _notify_delete_failure(client, cfg=cfg, chat_id=chat_id, reason=err)
