"""MTProto invite-link join and admin promotion for Elevate Admin group creation."""

from __future__ import annotations

import logging
from typing import Any

from telethon import TelegramClient
from telethon.errors import RPCError
from telethon.tl.functions.channels import EditAdminRequest
from telethon.tl.types import ChatAdminRights

from bot.services.mtproto_group_create import (
    _with_single_flood_retry,
    get_mtproto_lock,
    make_client,
)
from club_gc_settings import ClubGcConfig

logger = logging.getLogger(__name__)

# Megagroup-safe admin rights (omit broadcast-only flags like post_messages).
_MEGAGROUP_ADMIN_RIGHTS = ChatAdminRights(
    change_info=True,
    delete_messages=True,
    ban_users=True,
    invite_users=True,
    pin_messages=True,
    manage_call=True,
)


def parse_invite_hash(invite_link: str) -> str | None:
    """Extract Telegram invite hash from ``t.me/+…``, ``t.me/joinchat/…``, or ``+HASH``."""

    s = (invite_link or "").strip()
    if not s:
        return None
    hash_part = ""
    if "t.me/" in s:
        tail = s.split("t.me/", 1)[1]
        tail = tail.split("?", 1)[0].strip().lstrip("/")
        if tail.startswith("+"):
            hash_part = tail[1:]
        elif tail.lower().startswith("joinchat/"):
            parts = tail.split("/", 1)
            if len(parts) == 2:
                hash_part = parts[1]
    elif s.startswith("+"):
        hash_part = s[1:]
    hash_part = hash_part.strip()
    return hash_part or None


async def join_chat_via_invite_link(
    client: TelegramClient,
    invite_link: str,
) -> tuple[Any | None, str | None]:
    """Join a megagroup via invite link; return ``(channel_entity, error)``."""

    from telethon.tl import functions
    from telethon.utils import get_peer_id

    hash_part = parse_invite_hash(invite_link)
    if not hash_part:
        return None, "invalid_invite_link"

    try:
        checked = await _with_single_flood_retry(
            "CheckChatInviteRequest",
            lambda: client(functions.messages.CheckChatInviteRequest(hash_part)),
        )
    except Exception as e:
        err = getattr(e, "message", None) or type(e).__name__
        logger.info("CheckChatInviteRequest failed: %s", type(e).__name__)
        return None, str(err)[:500]

    channel = getattr(checked, "chat", None)
    if channel is not None:
        try:
            ent = await client.get_entity(channel)
            return ent, None
        except Exception as e:
            logger.info("get_entity after CheckChatInvite: %s", type(e).__name__)
            return channel, None

    try:
        upd = await _with_single_flood_retry(
            "ImportChatInviteRequest",
            lambda: client(functions.messages.ImportChatInviteRequest(hash_part)),
        )
        chats = getattr(upd, "chats", None) or []
        if not chats:
            return None, "import_no_chats"
        channel = chats[0]
        ent = await client.get_entity(channel)
        _ = get_peer_id(ent)
        return ent, None
    except Exception as e:
        err = getattr(e, "message", None) or type(e).__name__
        if isinstance(e, RPCError):
            err = getattr(e, "message", err) or type(e).__name__
        logger.info("ImportChatInviteRequest failed: %s", type(e).__name__)
        return None, str(err)[:500]


async def promote_megagroup_admin(
    client: TelegramClient,
    channel_entity,
    user_marker: str,
    *,
    rank: str = "Admin",
) -> tuple[bool, str | None]:
    """Promote ``user_marker`` to megagroup admin via ``EditAdminRequest``."""

    marker = (user_marker or "").strip()
    if not marker:
        return False, "empty_marker"
    lookup = marker if marker.startswith("@") or marker.lstrip("-").isdigit() else f"@{marker.lstrip('@')}"

    try:
        user_ent = await _with_single_flood_retry(
            f"get_entity:{lookup}",
            lambda: client.get_entity(lookup),
        )
        await _with_single_flood_retry(
            "EditAdminRequest",
            lambda: client(
                EditAdminRequest(
                    channel=channel_entity,
                    user_id=user_ent,
                    admin_rights=_MEGAGROUP_ADMIN_RIGHTS,
                    rank=rank,
                )
            ),
        )
        return True, None
    except Exception as e:
        err = getattr(e, "message", None) or type(e).__name__
        if isinstance(e, RPCError):
            err = getattr(e, "message", err) or type(e).__name__
        logger.info("promote_megagroup_admin failed marker=%s: %s", lookup, type(e).__name__)
        return False, str(err)[:500]


async def run_link_join_and_promote(
    creator_cfg: ClubGcConfig,
    *,
    channel_entity,
    invite_link: str | None,
    promote_marker: str,
    link_join_cfg: ClubGcConfig,
) -> tuple[list[dict], list[dict], list[dict]]:
    """RTS2 link-join + admin promote after megagroup creation. Best-effort."""

    link_joined: list[dict] = []
    promoted: list[dict] = []
    failures: list[dict] = []

    link = (invite_link or "").strip()
    if not link:
        failures.append(
            {"user": promote_marker, "reason": "no_invite_link", "kind": "link_join"}
        )
        return link_joined, promoted, failures

    joined_entity = channel_entity
    async with get_mtproto_lock(link_join_cfg.club_key):
        join_client = make_client(link_join_cfg)
        await join_client.connect()
        try:
            if not await join_client.is_user_authorized():
                failures.append(
                    {
                        "user": promote_marker,
                        "reason": "link_join_session_not_authorized",
                        "kind": "link_join",
                    }
                )
                return link_joined, promoted, failures
            ent, join_err = await join_chat_via_invite_link(join_client, link)
            if ent is None:
                failures.append(
                    {
                        "user": promote_marker,
                        "reason": join_err or "link_join_failed",
                        "kind": "link_join",
                    }
                )
                return link_joined, promoted, failures
            joined_entity = ent
            link_joined.append({"user": promote_marker, "kind": "link_join"})
        finally:
            await join_client.disconnect()

    async with get_mtproto_lock(creator_cfg.club_key):
        promote_client = make_client(creator_cfg)
        await promote_client.connect()
        try:
            if not await promote_client.is_user_authorized():
                failures.append(
                    {
                        "user": promote_marker,
                        "reason": "creator_session_not_authorized",
                        "kind": "promote",
                    }
                )
                return link_joined, promoted, failures
            ok, prom_err = await promote_megagroup_admin(
                promote_client,
                joined_entity,
                promote_marker,
            )
            if ok:
                promoted.append({"user": promote_marker, "kind": "admin"})
            else:
                failures.append(
                    {
                        "user": promote_marker,
                        "reason": prom_err or "promote_failed",
                        "kind": "promote",
                    }
                )
        finally:
            await promote_client.disconnect()

    return link_joined, promoted, failures
