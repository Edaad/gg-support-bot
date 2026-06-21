"""Shared Telethon direct-add logic for migrated supergroup recovery."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from scripts.backfill_support_group_invite_links import LinkedGroupRow, _gc_display_name

logger = logging.getLogger(__name__)

UserKind = Literal["player", "staff", "bot"]
FloodWaitPolicy = Literal["retry", "abort"]

FloodWaitObserver = Callable[[str, int], Awaitable[None]]
_flood_wait_observer: FloodWaitObserver | None = None
_flood_wait_policy: FloodWaitPolicy = "retry"


class FloodWaitAbortError(Exception):
    """Raised when flood-wait policy is abort (migration recovery halt)."""

    def __init__(self, wait_s: int, label: str) -> None:
        self.wait_s = int(wait_s)
        self.label = str(label)
        super().__init__(f"FloodWait {wait_s}s during {label}")


def set_flood_wait_policy(policy: FloodWaitPolicy) -> None:
    global _flood_wait_policy
    _flood_wait_policy = policy


def get_flood_wait_policy() -> FloodWaitPolicy:
    return _flood_wait_policy


@dataclass(frozen=True)
class ReaddTarget:
    kind: UserKind
    marker: str
    telegram_user_id: int | None = None


@dataclass
class ReaddGroupResult:
    chat_id: int
    club_id: int
    club_key: str | None
    title: str
    member_count_before: int
    member_count_after: int | None
    status: str
    added: list[str] = field(default_factory=list)
    already_member: list[str] = field(default_factory=list)
    privacy_blocked: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    invite_link: str | None = None
    error: str | None = None
    resolved_player_id: int | None = None
    resolved_player_username: str | None = None
    resolved_player_display_name: str | None = None
    resolved_player_source: str | None = None


@dataclass
class ElevateJoinResult:
    joined: bool = False
    already_member: bool = False
    error: str | None = None
    dry_run: bool = False


def set_flood_wait_observer(observer: FloodWaitObserver | None) -> None:
    global _flood_wait_observer
    _flood_wait_observer = observer


async def _sleep_flood_wait(exc: BaseException, *, label: str) -> None:
    from telethon.errors import FloodWaitError

    if not isinstance(exc, FloodWaitError):
        raise exc
    wait_s = int(getattr(exc, "seconds", 0) or 0)
    logger.info("Telegram FloodWait %ss (%s); sleeping…", wait_s, label)
    observer = _flood_wait_observer
    if observer is not None:
        try:
            await observer(label, wait_s)
        except Exception:
            logger.warning(
                "migration_group_readd: flood_wait observer failed label=%s",
                label,
                exc_info=True,
            )
    if _flood_wait_policy == "abort":
        raise FloodWaitAbortError(wait_s, label)
    await asyncio.sleep(float(wait_s) + 1.0)


async def call_with_flood_retry(coro_factory, *, label: str):
    while True:
        try:
            return await coro_factory()
        except Exception as e:
            from telethon.errors import FloodWaitError

            if isinstance(e, FloodWaitError):
                await _sleep_flood_wait(e, label=label)
                continue
            raise


async def participant_count(client, entity) -> int:
    from telethon.tl.functions.channels import GetFullChannelRequest
    from telethon.tl.functions.messages import GetFullChatRequest
    from telethon.tl.types import Channel, Chat

    if isinstance(entity, Channel):
        full = await call_with_flood_retry(
            lambda: client(GetFullChannelRequest(entity)),
            label="GetFullChannelRequest",
        )
        return int(full.full_chat.participants_count or 0)
    if isinstance(entity, Chat):
        full = await call_with_flood_retry(
            lambda: client(GetFullChatRequest(entity.id)),
            label="GetFullChatRequest",
        )
        participants = getattr(full.full_chat, "participants", None)
        if participants is not None and hasattr(participants, "participants"):
            return len(participants.participants)
        return int(getattr(full.full_chat, "participants_count", 0) or 0)
    return 0


async def participant_user_ids(client, entity) -> set[int]:
    parts = await call_with_flood_retry(
        lambda: client.get_participants(entity, limit=200),
        label="get_participants",
    )
    return {int(p.id) for p in parts}


def load_player_rows_by_chat(
    chat_ids: set[int],
) -> dict[int, tuple[int | None, str | None, str | None]]:
    """Map chat_id -> (player_telegram_user_id, player_username, club_key)."""
    if not chat_ids:
        return {}
    from db.connection import get_db
    from db.models import SupportGroupChat
    from notification.chat_id import telegram_chat_id_variants

    variants_to_canonical: dict[int, int] = {}
    for cid in chat_ids:
        for v in telegram_chat_id_variants(int(cid)):
            variants_to_canonical[int(v)] = int(cid)

    out: dict[int, tuple[int | None, str | None, str | None]] = {}
    with get_db() as session:
        rows = (
            session.query(
                SupportGroupChat.telegram_chat_id,
                SupportGroupChat.player_telegram_user_id,
                SupportGroupChat.player_username,
                SupportGroupChat.club_key,
            )
            .filter(SupportGroupChat.telegram_chat_id.in_(list(variants_to_canonical.keys())))
            .order_by(SupportGroupChat.created_at.desc())
            .all()
        )
    for raw_cid, player_id, player_username, club_key in rows:
        canonical = variants_to_canonical.get(int(raw_cid))
        if canonical is None or canonical in out:
            continue
        pid = int(player_id) if player_id is not None else None
        out[canonical] = (pid, (player_username or None), (club_key or None))
    return out


def load_player_display_names_by_chat(chat_ids: set[int]) -> dict[int, str | None]:
    if not chat_ids:
        return {}
    from db.connection import get_db
    from db.models import SupportGroupChat
    from notification.chat_id import telegram_chat_id_variants

    variants_to_canonical: dict[int, int] = {}
    for cid in chat_ids:
        for v in telegram_chat_id_variants(int(cid)):
            variants_to_canonical[int(v)] = int(cid)

    out: dict[int, str | None] = {}
    with get_db() as session:
        rows = (
            session.query(
                SupportGroupChat.telegram_chat_id,
                SupportGroupChat.player_display_name,
            )
            .filter(SupportGroupChat.telegram_chat_id.in_(list(variants_to_canonical.keys())))
            .order_by(SupportGroupChat.created_at.desc())
            .all()
        )
    for raw_cid, display_name in rows:
        canonical = variants_to_canonical.get(int(raw_cid))
        if canonical is None or canonical in out:
            continue
        out[canonical] = (display_name or None)
    return out


def staff_invite_markers(cfg) -> list[str]:
    from club_gc_settings import get_gc_users_to_add

    markers: list[str] = []
    seen: set[str] = set()
    for raw in list(get_gc_users_to_add(cfg)) + (
        [cfg.bot_account] if cfg.bot_account else []
    ):
        marker = (raw or "").strip()
        if not marker:
            continue
        norm = marker.lower().lstrip("@")
        if norm in seen:
            continue
        seen.add(norm)
        markers.append(marker)
    return markers


def is_privacy_error(reason: str | None) -> bool:
    low = (reason or "").lower()
    return "privacy" in low or "user_privacy" in low


def error_label(exc: BaseException) -> str:
    msg = str(exc).strip().replace("\n", " ")
    if len(msg) > 200:
        msg = msg[:197] + "..."
    name = type(exc).__name__
    return f"{name}: {msg}" if msg else name


def is_entity_resolution_error(exc: BaseException) -> bool:
    """True when Telethon cannot resolve a user/chat id or username (non-fatal)."""
    if type(exc).__name__ == "UsernameNotOccupiedError":
        return True
    low = str(exc).lower()
    return (
        "could not find the input entity" in low
        or "no user has" in low
        or "username is not in use" in low
    )


def _username_marker(stored_username: str | None) -> str | None:
    raw = (stored_username or "").strip()
    if not raw or raw.isdigit():
        return None
    return raw if raw.startswith("@") else f"@{raw.lstrip('@')}"


async def resolve_player_entity_for_readd(
    client,
    channel_entity,
    cfg,
    *,
    stored_id: int,
    stored_username: str | None,
    self_id: int | None,
    old_chat_id: int | None = None,
) -> tuple[Any | None, str]:
    """Resolve a Telethon user for direct-add; message scan is last resort."""
    from bot.services.mtproto_group_player import find_latest_eligible_message_sender

    try:
        user = await call_with_flood_retry(
            lambda: client.get_entity(int(stored_id)),
            label=f"get_entity:{stored_id}",
        )
        return user, "stored_id"
    except FloodWaitAbortError:
        raise
    except Exception as e:
        if not is_entity_resolution_error(e):
            raise

    username_marker = _username_marker(stored_username)
    if username_marker:
        try:
            user = await call_with_flood_retry(
                lambda: client.get_entity(username_marker),
                label=f"get_entity:{username_marker}",
            )
            return user, "username"
        except FloodWaitAbortError:
            raise
        except Exception as e:
            if not is_entity_resolution_error(e):
                raise

    user = await find_latest_eligible_message_sender(
        client,
        channel_entity,
        cfg,
        self_id=self_id,
    )
    if user is not None:
        return user, "message_sender"

    if old_chat_id is not None:
        try:
            old_entity = await call_with_flood_retry(
                lambda: client.get_entity(int(old_chat_id)),
                label=f"get_entity:old_chat:{old_chat_id}",
            )
        except FloodWaitAbortError:
            raise
        except Exception as e:
            logger.warning(
                "resolve_player: open old_chat_id=%s failed: %s",
                old_chat_id,
                error_label(e),
            )
        else:
            user = await find_latest_eligible_message_sender(
                client,
                old_entity,
                cfg,
                self_id=self_id,
            )
            if user is not None:
                return user, "old_chat_message_sender"

    return None, "unresolved"


async def invite_user_id(
    client,
    channel_entity,
    user_id: int,
    *,
    apply: bool,
    user_entity: Any | None = None,
) -> tuple[str, str | None]:
    """Return (status, reason). status: added | already_member | privacy | failed | dry_run."""
    from telethon.errors.rpcerrorlist import UserAlreadyParticipantError, UserNotParticipantError
    from telethon.tl.functions.channels import GetParticipantRequest, InviteToChannelRequest

    if not apply:
        return "dry_run", None

    user = user_entity
    if user is None:
        try:
            user = await call_with_flood_retry(
                lambda: client.get_entity(int(user_id)),
                label=f"get_entity:{user_id}",
            )
        except FloodWaitAbortError:
            raise
        except Exception as e:
            return "failed", error_label(e)

    try:
        await call_with_flood_retry(
            lambda: client(GetParticipantRequest(channel_entity, user)),
            label=f"GetParticipant:{user_id}",
        )
        return "already_member", None
    except UserNotParticipantError:
        pass
    except FloodWaitAbortError:
        raise
    except Exception:
        pass

    try:
        await call_with_flood_retry(
            lambda: client(InviteToChannelRequest(channel_entity, [user])),
            label=f"InviteToChannel:{user_id}",
        )
        return "added", None
    except UserAlreadyParticipantError:
        return "already_member", None
    except FloodWaitAbortError:
        raise
    except Exception as e:
        reason = error_label(e)
        if is_privacy_error(reason):
            return "privacy", reason
        return "failed", reason


async def invite_marker(
    client,
    channel_entity,
    marker: str,
    *,
    apply: bool,
) -> tuple[str, str | None]:
    from telethon.errors.rpcerrorlist import UserAlreadyParticipantError
    from telethon.tl.functions.channels import InviteToChannelRequest

    if not apply:
        return "dry_run", None
    try:
        user = await call_with_flood_retry(
            lambda: client.get_entity(marker.strip()),
            label=f"get_entity:{marker}",
        )
    except FloodWaitAbortError:
        raise
    except Exception as e:
        return "failed", error_label(e)
    try:
        await call_with_flood_retry(
            lambda: client(InviteToChannelRequest(channel_entity, [user])),
            label=f"InviteToChannel:{marker}",
        )
        return "added", None
    except UserAlreadyParticipantError:
        return "already_member", None
    except FloodWaitAbortError:
        raise
    except Exception as e:
        reason = error_label(e)
        if is_privacy_error(reason):
            return "privacy", reason
        return "failed", reason


async def export_invite_link(client, entity) -> str | None:
    from bot.services.mtproto_group_create import export_invite_link_for_peer

    try:
        return await call_with_flood_retry(
            lambda: export_invite_link_for_peer(client, entity),
            label="export_invite_link",
        )
    except Exception as e:
        logger.warning("export_invite_link failed: %s", error_label(e))
        return None


async def readd_group(
    *,
    client,
    cfg,
    group: LinkedGroupRow,
    dialog_chat_id: int,
    player_id: int | None,
    player_username: str | None,
    apply: bool,
    update_invite_links: bool,
    invite_staff: bool,
    listener_user_id: int | None,
    old_chat_id: int | None = None,
    export_invite_link_always: bool = False,
) -> ReaddGroupResult:
    title = _gc_display_name(group.title, group.chat_id)
    result = ReaddGroupResult(
        chat_id=int(group.chat_id),
        club_id=int(group.club_id),
        club_key=cfg.club_key,
        title=title,
        member_count_before=0,
        member_count_after=None,
        status="pending",
    )

    try:
        entity = await call_with_flood_retry(
            lambda: client.get_entity(int(dialog_chat_id)),
            label=f"get_entity:{dialog_chat_id}",
        )

        if not invite_staff:
            if player_id is None:
                result.status = "no_targets"
                return result

            from bot.services.mtproto_group_player import format_telegram_user_display

            resolved_user, source = await resolve_player_entity_for_readd(
                client,
                entity,
                cfg,
                stored_id=int(player_id),
                stored_username=player_username,
                self_id=listener_user_id,
                old_chat_id=old_chat_id,
            )
            if resolved_user is None:
                marker = f"@{player_username}" if player_username else str(player_id)
                label = f"player:{marker}"
                result.failed.append(f"{label}:entity_resolution_failed")
                result.status = "partial"
                return result

            resolved_id = int(getattr(resolved_user, "id", player_id))
            display_name, at_username = format_telegram_user_display(resolved_user)
            marker = at_username or (f"@{player_username}" if player_username else str(resolved_id))
            label = f"player:{marker}"
            result.resolved_player_id = resolved_id
            result.resolved_player_username = (at_username or "").lstrip("@") or None
            result.resolved_player_display_name = display_name
            result.resolved_player_source = source

            status, reason = await invite_user_id(
                client,
                entity,
                resolved_id,
                apply=apply,
                user_entity=resolved_user,
            )
            needs_invite_export = False
            if status == "added":
                result.added.append(label)
            elif status == "already_member":
                result.already_member.append(label)
            elif status == "privacy":
                result.privacy_blocked.append(label)
                needs_invite_export = True
            elif status == "dry_run":
                result.added.append(f"would_add:{label}")
            else:
                result.failed.append(f"{label}:{reason or 'unknown'}")
                if is_privacy_error(reason):
                    needs_invite_export = True

            if needs_invite_export and apply:
                invite_link = await export_invite_link(client, entity)
                result.invite_link = invite_link
                if invite_link and update_invite_links:
                    from bot.services.support_group_chats import upsert_support_group_invite_link

                    upsert_support_group_invite_link(
                        club_key=cfg.club_key,
                        club_display_name=cfg.club_display_name,
                        telegram_chat_id=int(group.chat_id),
                        telegram_chat_title=title,
                        invite_link=invite_link,
                        mtproto_session_name=cfg.mtproto_session,
                    )

            if export_invite_link_always and apply and not result.invite_link:
                invite_link = await export_invite_link(client, entity)
                result.invite_link = invite_link
                if invite_link and update_invite_links:
                    from bot.services.support_group_chats import upsert_support_group_invite_link

                    upsert_support_group_invite_link(
                        club_key=cfg.club_key,
                        club_display_name=cfg.club_display_name,
                        telegram_chat_id=int(group.chat_id),
                        telegram_chat_title=title,
                        invite_link=invite_link,
                        mtproto_session_name=cfg.mtproto_session,
                    )
            elif export_invite_link_always and not apply:
                result.added.append("would_export:invite_link")

            if result.privacy_blocked:
                result.status = "privacy_fallback"
            elif result.failed:
                result.status = "partial"
            elif result.added or result.already_member:
                result.status = "ok" if apply else "would_readd"
            else:
                result.status = "no_targets"
            return result

        member_ids = await participant_user_ids(client, entity)
        result.member_count_before = len(member_ids)

        targets: list[ReaddTarget] = []
        if player_id is not None:
            marker = f"@{player_username}" if player_username else str(player_id)
            targets.append(
                ReaddTarget(kind="player", marker=marker, telegram_user_id=int(player_id))
            )

        if invite_staff:
            for staff_marker in staff_invite_markers(cfg):
                norm = staff_marker.lstrip("@").lower()
                if listener_user_id is not None and norm == str(listener_user_id):
                    continue
                kind: UserKind = "bot" if "bot" in norm else "staff"
                targets.append(ReaddTarget(kind=kind, marker=staff_marker))

        needs_invite_export = False

        for target in targets:
            if target.telegram_user_id is not None and target.telegram_user_id in member_ids:
                result.already_member.append(f"{target.kind}:{target.marker}")
                continue
            if target.telegram_user_id is not None:
                status, reason = await invite_user_id(
                    client,
                    entity,
                    int(target.telegram_user_id),
                    apply=apply,
                )
            else:
                status, reason = await invite_marker(
                    client,
                    entity,
                    target.marker,
                    apply=apply,
                )

            label = f"{target.kind}:{target.marker}"
            if status == "added":
                result.added.append(label)
                if target.telegram_user_id is not None:
                    member_ids.add(int(target.telegram_user_id))
            elif status == "already_member":
                result.already_member.append(label)
            elif status == "privacy":
                result.privacy_blocked.append(label)
                needs_invite_export = True
            elif status == "dry_run":
                result.added.append(f"would_add:{label}")
            else:
                result.failed.append(f"{label}:{reason or 'unknown'}")
                if is_privacy_error(reason):
                    needs_invite_export = True

        if needs_invite_export and apply:
            invite_link = await export_invite_link(client, entity)
            result.invite_link = invite_link
            if invite_link and update_invite_links:
                from bot.services.support_group_chats import upsert_support_group_invite_link

                upsert_support_group_invite_link(
                    club_key=cfg.club_key,
                    club_display_name=cfg.club_display_name,
                    telegram_chat_id=int(group.chat_id),
                    telegram_chat_title=title,
                    invite_link=invite_link,
                    mtproto_session_name=cfg.mtproto_session,
                )

        if apply:
            result.member_count_after = len(await participant_user_ids(client, entity))
        else:
            result.member_count_after = None

        if result.privacy_blocked:
            result.status = "privacy_fallback"
        elif result.failed:
            result.status = "partial"
        elif result.added or result.already_member:
            result.status = "ok" if apply else "would_readd"
        else:
            result.status = "no_targets"
    except FloodWaitAbortError:
        raise
    except Exception as e:
        result.status = "error"
        result.error = error_label(e)

    return result


async def readd_round_table_player_and_link(
    *,
    client,
    cfg,
    group: LinkedGroupRow,
    dialog_chat_id: int,
    player_id: int | None,
    player_username: str | None,
    apply: bool,
    update_invite_links: bool,
    listener_user_id: int | None,
    old_chat_id: int | None = None,
) -> ReaddGroupResult:
    """RT recovery pass: direct-add player and always export invite link."""

    return await readd_group(
        client=client,
        cfg=cfg,
        group=group,
        dialog_chat_id=dialog_chat_id,
        player_id=player_id,
        player_username=player_username,
        apply=apply,
        update_invite_links=update_invite_links,
        invite_staff=False,
        listener_user_id=listener_user_id,
        old_chat_id=old_chat_id,
        export_invite_link_always=True,
    )


async def _elevate_user_id(elevate_client) -> int | None:
    try:
        me = await elevate_client.get_me()
        if me and getattr(me, "id", None):
            return int(me.id)
    except Exception:
        logger.warning("elevate_join: get_me failed", exc_info=True)
    return None


async def elevate_join_recovery_group(
    *,
    invite_link: str,
    dialog_chat_id: int,
    rt_client,
    apply: bool,
) -> ElevateJoinResult:
    """Join a migrated GC via invite link using the Elevate Admin session."""

    from club_gc_settings import get_mtproto_session_config
    from bot.services.mtproto_group_create import get_mtproto_lock, make_client
    from bot.services.mtproto_group_join import join_chat_via_invite_link

    link = (invite_link or "").strip()
    if not link:
        return ElevateJoinResult(error="no_invite_link")

    elevate_cfg = get_mtproto_session_config("elevate_admin")
    if elevate_cfg is None:
        return ElevateJoinResult(error="no_elevate_config")

    if not apply:
        return ElevateJoinResult(dry_run=True)

    try:
        entity = await call_with_flood_retry(
            lambda: rt_client.get_entity(int(dialog_chat_id)),
            label=f"get_entity:{dialog_chat_id}",
        )
    except FloodWaitAbortError:
        raise
    except Exception as e:
        return ElevateJoinResult(error=error_label(e))

    async with get_mtproto_lock("elevate_admin"):
        elevate_client = make_client(elevate_cfg)
        await elevate_client.connect()
        try:
            if not await elevate_client.is_user_authorized():
                return ElevateJoinResult(error="elevate_session_not_authorized")

            elevate_id = await _elevate_user_id(elevate_client)
            if elevate_id is not None:
                member_ids = await participant_user_ids(rt_client, entity)
                if elevate_id in member_ids:
                    return ElevateJoinResult(already_member=True)

            _ent, join_err = await join_chat_via_invite_link(elevate_client, link)
            if join_err:
                return ElevateJoinResult(error=join_err)

            if elevate_id is not None:
                member_ids = await participant_user_ids(rt_client, entity)
                if elevate_id in member_ids:
                    return ElevateJoinResult(joined=True)

            return ElevateJoinResult(joined=True)
        finally:
            await elevate_client.disconnect()
