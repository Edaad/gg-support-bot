"""MTProto (Telethon) helpers for `/gc`: auth, basic group creation, invites, photo, invite link."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal
from dataclasses import dataclass, field
from pathlib import Path

from telethon import TelegramClient, utils
from telethon.errors import FloodWaitError, RPCError, SessionPasswordNeededError
from telethon.errors.rpcerrorlist import UserAlreadyParticipantError
from telethon.sessions import StringSession
from telethon.tl.functions.channels import (
    EditPhotoRequest,
    GetParticipantRequest,
    InviteToChannelRequest,
)
from telethon.tl.functions.messages import AddChatUserRequest, CreateChatRequest, EditChatPhotoRequest
from telethon.tl.types import Channel, Chat, InputChatUploadedPhoto, User

from club_gc_settings import (
    ClubGcConfig,
    get_gc_users_to_add,
    get_mtproto_telethon_client_kwargs,
    get_tg_mtproto_credentials,
    link_join_exclude_normalized,
    resolve_group_creator_cfg,
    resolve_link_join_cfg,
)
from bot.services.mtproto_session_db import load_session_string_for_club


import os

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _assert_not_web_dyno(operation: str) -> None:
    dyno = os.getenv("DYNO", "")
    if dyno.startswith("web"):
        raise RuntimeError(
            f"Telethon operation '{operation}' called on web dyno ({dyno!r}). "
            "This reuses the worker's production session from a different IP and causes "
            "AuthKeyDuplicatedError. Read session status from the DB instead."
        )
FLOODWAIT_MAX_SECONDS = 120

# Telegram channel / megagroup title limit (characters).
_TITLE_MAX_CHARS = 255

# Short prefix segment before literal `` / / `` — must match ops naming (Round Table → RT, etc.).
_TITLE_PREFIX_BY_CLUB: dict[str, str] = {
    "round_table": "RT",
    "creator_club": "CC",
    "clubgto": "GTO",
}

_mtproto_locks: dict[str, asyncio.Lock] = {}


def get_mtproto_lock(club_key: str) -> asyncio.Lock:
    if club_key not in _mtproto_locks:
        _mtproto_locks[club_key] = asyncio.Lock()
    return _mtproto_locks[club_key]


def resolve_repo_path(rel_or_abs: str) -> Path:
    p = Path(rel_or_abs)
    if p.is_absolute():
        return p
    return (REPO_ROOT / p).resolve()


def normalize_invite_link(link: str | None) -> str | None:
    """Normalize empty-ish invite links."""
    if not link:
        return None
    s = link.strip()
    return s if s else None


def megagroup_title_prefix_for_club(cfg: ClubGcConfig) -> str:
    """First segment before `` / / `` (e.g. RT, CC, GTO)."""

    return _TITLE_PREFIX_BY_CLUB.get(cfg.club_key, cfg.club_key[:3].upper())


def player_label_for_support_megagroup_title(player_user) -> str:
    """Human-readable tail for titles: @username → full name → first name → New Player."""

    if player_user is None:
        return "New Player"
    un = getattr(player_user, "username", None)
    if isinstance(un, str) and un.strip():
        return f"@{un.strip().lstrip('@')}"
    fn = (getattr(player_user, "first_name", None) or "").strip()
    ln = (getattr(player_user, "last_name", None) or "").strip()
    if fn and ln:
        return f"{fn} {ln}"
    if fn:
        return fn
    return "New Player"


def build_support_megagroup_title(cfg: ClubGcConfig, player_user) -> str:
    """``RT / / @player`` format — two slashes separated by spaces: `` / / ``."""

    prefix = megagroup_title_prefix_for_club(cfg)
    sep = " / / "
    label = player_label_for_support_megagroup_title(player_user)
    title = f"{prefix}{sep}{label}"
    if len(title) <= _TITLE_MAX_CHARS:
        return title

    reserve = len(prefix) + len(sep)
    max_label = _TITLE_MAX_CHARS - reserve
    if max_label < 4:
        return title[: _TITLE_MAX_CHARS]
    shortened = label[:max_label].rstrip()
    return f"{prefix}{sep}{shortened}"[:_TITLE_MAX_CHARS]


def make_client(cfg: ClubGcConfig, *, prefer_database: bool = True) -> TelegramClient:
    """Build a Telethon client.

    ``prefer_database=False`` forces the on-disk SQLite ``.session`` file and is used **only**
    for the Dashboard SMS/2FA handshake so a stale Postgres row cannot block ``SendCode``.

    Reconnect tuning via ``GC_MTPROTO_*`` env vars (see ``get_mtproto_telethon_client_kwargs``).
    """
    api_id, api_hash = get_tg_mtproto_credentials()
    telethon_kw = get_mtproto_telethon_client_kwargs()
    if prefer_database:
        db_string = load_session_string_for_club(cfg.club_key)
        if db_string:
            return TelegramClient(
                StringSession(db_string),
                api_id=api_id,
                api_hash=api_hash,
                **telethon_kw,
            )

    resolved = resolve_repo_path(cfg.mtproto_session)
    if resolved.suffix == ".session":
        stem = resolved.with_suffix("")
    else:
        stem = resolved
    stem.resolve().parent.mkdir(parents=True, exist_ok=True)
    session_arg = stem.as_posix()
    return TelegramClient(session_arg, api_id=api_id, api_hash=api_hash, **telethon_kw)


async def _with_single_flood_retry(tag: str, coro_factory):
    """Runs coro_factory(); if FloodWait is within threshold, sleeps once then retries once."""

    for attempt in range(2):
        try:
            return await coro_factory()
        except FloodWaitError as e:
            if e.seconds > FLOODWAIT_MAX_SECONDS:
                logger.warning("%s FloodWait exceeded cap: %ss", tag, e.seconds)
                raise RuntimeError(
                    f"Telegram rate limits: please wait ~{e.seconds}s and try /gc again."
                ) from e
            logger.info("%s FloodWait %ss, sleeping then retry=%s", tag, e.seconds, attempt)
            await asyncio.sleep(float(e.seconds) + 1.0)
            if attempt == 1:
                raise RuntimeError(f"Still rate limited after retry ({tag}).") from e


async def is_client_authorized(cfg: ClubGcConfig) -> bool:
    _assert_not_web_dyno("is_client_authorized")
    client = make_client(cfg)
    await client.connect()
    try:
        return await client.is_user_authorized()
    finally:
        await client.disconnect()


async def send_code_for_phone(cfg: ClubGcConfig, phone: str) -> str:
    """Request Telegram login code; returns ``phone_code_hash`` (caller stores — never logged).

    **No FloodWait auto-retry:** a second ``SendCodeRequest`` invalidates the previous code.
    If Telegram rate-limits, we surface a single message so the next ``/gc`` does one fresh send.
    """

    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg, prefer_database=False)
        await client.connect()
        try:
            try:
                sent = await client.send_code_request(phone.strip())
            except FloodWaitError as e:
                if e.seconds > FLOODWAIT_MAX_SECONDS:
                    logger.warning("send_code FloodWait too long: %ss", e.seconds)
                    raise RuntimeError(
                        f"Telegram rate limit: wait ~{e.seconds}s, then run /gc once (only one code request)."
                    ) from e
                logger.info(
                    "send_code FloodWait %ss club=%s — do not parallel /gc; wait then one /gc",
                    e.seconds,
                    cfg.club_key,
                )
                raise RuntimeError(
                    f"Telegram asked to wait ~{e.seconds}s before another login code. "
                    "Wait, then send /gc exactly once (asking again invalidates the earlier code)."
                ) from e

            hash_value = getattr(sent, "phone_code_hash", None)
            if not hash_value:
                raise RuntimeError("Telegram returned no phone_code_hash (cannot continue login).")
            logger.info("MTProto SendCode succeeded club=%s", cfg.club_key)
            return str(hash_value)
        finally:
            await client.disconnect()


async def authenticate_mtproto_code(
    cfg: ClubGcConfig,
    *,
    phone: str,
    code: str,
    phone_code_hash: str,
) -> None:
    """Submit SMS code. Raises ``SessionPasswordNeededError`` when Telegram needs the Cloud Password."""

    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg, prefer_database=False)
        await client.connect()
        try:
            # Single sign_in only — retrying can interact badly with Telegram login state.
            try:
                await client.sign_in(
                    phone.strip(),
                    code.strip(),
                    phone_code_hash=phone_code_hash,
                )
            except FloodWaitError as e:
                raise RuntimeError(
                    f"Telegram rate limit during sign-in (~{e.seconds}s). Wait, then run /gc for a new code."
                ) from e

            authorized = await client.is_user_authorized()
            if not authorized:
                raise RuntimeError("Sign-in did not authorize the session.")

        finally:
            await client.disconnect()


async def authenticate_mtproto_password(cfg: ClubGcConfig, *, password: str) -> None:
    """Finish interactive login using the account Cloud Password after ``SessionPasswordNeededError``."""

    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg, prefer_database=False)
        await client.connect()
        try:
            try:
                await client.sign_in(password=str(password))
            except FloodWaitError as e:
                raise RuntimeError(
                    f"Telegram rate limit (~{e.seconds}s). Wait and try sending the password again."
                ) from e
            authorized = await client.is_user_authorized()
            if not authorized:
                raise RuntimeError("2FA succeeded but MTProto session is still not authorized.")

        finally:
            await client.disconnect()


def _is_channel_entity(entity: Any) -> bool:
    return isinstance(entity, Channel)


async def _is_user_in_group(
    client: TelegramClient, group_entity: Any, user_entity: Any
) -> bool:
    from telethon.errors.rpcerrorlist import UserNotParticipantError

    if _is_channel_entity(group_entity):
        try:
            await _with_single_flood_retry(
                "GetParticipantRequest",
                lambda: client(GetParticipantRequest(group_entity, user_entity)),
            )
            return True
        except UserNotParticipantError:
            return False
        except Exception:
            return False

    uid = getattr(user_entity, "id", None)
    if uid is None:
        return False
    try:
        async for participant in client.iter_participants(group_entity):
            if int(getattr(participant, "id", 0)) == int(uid):
                return True
    except Exception as e:
        logger.info("iter_participants membership check: %s", type(e).__name__)
    return False


async def _add_user_to_group(
    client: TelegramClient, group_entity: Any, user_entity: Any
) -> tuple[bool, str | None]:
    try:
        if _is_channel_entity(group_entity):
            await _with_single_flood_retry(
                "InviteToChannelRequest",
                lambda: client(
                    InviteToChannelRequest(channel=group_entity, users=[user_entity])
                ),
            )
        else:
            await _with_single_flood_retry(
                "AddChatUserRequest",
                lambda: client(
                    AddChatUserRequest(
                        chat_id=int(group_entity.id),
                        user_id=user_entity,
                        fwd_limit=50,
                    )
                ),
            )
        return True, None
    except UserAlreadyParticipantError:
        return True, None
    except Exception as e:
        low = repr(e).lower()
        readable: str | None = str(e).strip()
        if not readable:
            readable = type(e).__name__
        if isinstance(e, RPCError):
            readable = getattr(e, "message", readable) or readable
        if "privacy" in low or "user_privacy" in low:
            readable = "privacy restricted"
        readable = readable[:500]
        logger.info("Add user to group skipped: %s", type(e).__name__)
        return False, readable


async def _apply_group_photo_entity(
    client: TelegramClient,
    group_entity: Any,
    photo_abs: Path,
) -> None:
    uploaded = await client.upload_file(photo_abs.as_posix())
    photo = InputChatUploadedPhoto(file=uploaded)
    if _is_channel_entity(group_entity):
        inp = utils.get_input_channel(group_entity)
        await client(EditPhotoRequest(inp, photo))
        return
    await client(
        EditChatPhotoRequest(
            chat_id=int(group_entity.id),
            photo=photo,
        )
    )


def _user_marker_norm(user_entity: Any) -> str | None:
    un = getattr(user_entity, "username", None)
    if isinstance(un, str) and un.strip():
        return un.strip().lstrip("@").lower()
    uid = getattr(user_entity, "id", None)
    return str(uid) if uid is not None else None


def _marker_matches_user(marker: str, user_entity: Any) -> bool:
    raw = (marker or "").strip()
    if not raw:
        return False
    norm = raw.lstrip("@").lower()
    user_norm = _user_marker_norm(user_entity)
    if user_norm and norm == user_norm:
        return True
    uid = getattr(user_entity, "id", None)
    return uid is not None and raw.lstrip("-").isdigit() and int(raw) == int(uid)


def _looks_like_invitable_user(ent: Any) -> bool:
    if isinstance(ent, User):
        return True
    return (
        getattr(ent, "id", None) is not None
        and not _is_channel_entity(ent)
        and not isinstance(ent, Chat)
    )


async def _resolve_seed_user_for_create_chat(
    client: TelegramClient,
    *,
    player_user: Any | None,
    invite_targets: list[str],
) -> User | None:
    if player_user is not None:
        return player_user

    for marker in invite_targets:
        try:
            ent = await _with_single_flood_retry(
                f"get_entity:{marker}",
                lambda m=marker: client.get_entity(m.strip()),
            )
        except Exception:
            continue
        if _looks_like_invitable_user(ent):
            return ent
    return None


@dataclass
class MtProtoGroupOutcome:
    """Result of support group creation + post-setup."""

    ok: bool
    telegram_chat_id: int | None
    telegram_chat_title: str
    invite_link: str | None
    added_users: list[dict]
    failed_users: list[dict]
    initial_message_sent: bool
    group_photo_attempted: bool
    group_photo_ok: bool
    warnings: list[str] = field(default_factory=list)
    error_hint: str | None = None
    player_direct_add_ok: bool | None = None
    link_joined_users: list[dict] = field(default_factory=list)
    promoted_admins: list[dict] = field(default_factory=list)
    link_join_failures: list[dict] = field(default_factory=list)


async def apply_club_group_photo(
    client: TelegramClient,
    group_entity,
    cfg: ClubGcConfig,
    *,
    warnings: list[str] | None = None,
    failed_users: list[dict] | None = None,
) -> bool:
    """Best-effort: set group photo from ``cfg.group_photo_path``."""

    if not cfg.group_photo_path:
        return False

    photo_abs = resolve_repo_path(cfg.group_photo_path)
    if not photo_abs.exists():
        msg = f"group photo path missing on disk: {photo_abs.as_posix()} — skipped."
        if warnings is not None:
            warnings.append(msg)
        logger.warning("apply_club_group_photo: %s club=%s", msg, cfg.club_key)
        return False

    try:

        async def upload_group_photo():
            await _apply_group_photo_entity(client, group_entity, photo_abs)

        await _with_single_flood_retry("EditGroupPhoto", upload_group_photo)
        return True
    except Exception as e:
        err_name = type(e).__name__
        if warnings is not None:
            warnings.append(f"group photo upload failed ({err_name})")
        if failed_users is not None:
            failed_users.append(
                {"user": "__group_photo__", "reason": err_name, "kind": "photo"}
            )
        logger.warning(
            "apply_club_group_photo failed club=%s: %s", cfg.club_key, err_name
        )
        return False


async def ensure_player_in_support_group(
    client: TelegramClient,
    group_entity,
    player_user,
    cfg: ClubGcConfig,
) -> Literal["already_member", "invited_ok", "invite_failed"]:
    """Re-add flow: detect membership or best-effort invite."""

    if await _is_user_in_group(client, group_entity, player_user):
        return "already_member"

    ok, _ = await _add_user_to_group(client, group_entity, player_user)
    if ok:
        await apply_club_group_photo(client, group_entity, cfg)
    return "invited_ok" if ok else "invite_failed"


async def _export_invite_link(client: TelegramClient, peer) -> str:
    export_fn = getattr(client, "export_chat_invite_link", None)
    if callable(export_fn):
        return await export_fn(peer)

    from telethon.tl import functions

    inp = await client.get_input_entity(peer)
    inv = await _with_single_flood_retry(
        "ExportChatInvite",
        lambda: client(functions.messages.ExportChatInviteRequest(peer=inp)),
    )
    return inv.link


async def export_invite_link_for_peer(client: TelegramClient, peer) -> str | None:
    """Best-effort invite link for a group or channel entity."""
    try:
        return normalize_invite_link(await _export_invite_link(client, peer))
    except Exception as e:
        logger.info("export_invite_link_for_peer: %s", type(e).__name__)
        return None


def _player_log_marker(user_entity) -> str:
    un = getattr(user_entity, "username", None)
    if isinstance(un, str) and un.strip():
        return f"@{un.strip().lstrip('@')}"
    uid = getattr(user_entity, "id", None)
    return str(uid) if uid is not None else "player"


async def _invite_user_entity(
    client: TelegramClient, group_entity, user_entity
) -> tuple[bool, str | None]:
    return await _add_user_to_group(client, group_entity, user_entity)


async def _invite_one(
    client: TelegramClient, group_entity, marker: str
) -> tuple[bool, str | None]:
    try:
        ent = await _with_single_flood_retry(
            f"get_entity:{marker}",
            lambda: client.get_entity(marker.strip()),
        )
        if not _looks_like_invitable_user(ent):
            return False, "not_a_user"
        if not getattr(ent, "access_hash", None):
            return False, "missing access_hash"
        return await _add_user_to_group(client, group_entity, ent)
    except Exception as e:
        low = repr(e).lower()
        readable: str | None = str(e).strip()
        if not readable:
            readable = type(e).__name__
        if isinstance(e, RPCError):
            readable = getattr(e, "message", readable) or readable
        if "privacy" in low or "user_privacy" in low:
            readable = "privacy restricted"
        readable = readable[:500]
        logger.info("Invite skipped for %s: %s", marker, type(e).__name__)
        return False, readable


async def resolve_telegram_user_marker(
    cfg: ClubGcConfig,
    marker: str,
) -> tuple[Any | None, str | None]:
    """Resolve ``@username`` or numeric Telegram user id to a Telethon ``User``."""

    from telethon.tl.types import User

    raw = (marker or "").strip()
    if not raw:
        return None, "empty_marker"

    lookup = raw if raw.startswith("@") or raw.lstrip("-").isdigit() else f"@{raw.lstrip('@')}"

    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                return None, "not_authorized"
            ent = await _with_single_flood_retry(
                f"get_entity:{lookup}",
                lambda: client.get_entity(lookup),
            )
            if not isinstance(ent, User):
                return None, "not_a_user"
            if getattr(ent, "bot", False):
                return None, "is_bot"
            return ent, None
        except Exception as e:
            logger.info("resolve_telegram_user_marker failed marker=%s: %s", lookup, type(e).__name__)
            return None, type(e).__name__
        finally:
            await client.disconnect()


async def send_player_dm_via_club(cfg: ClubGcConfig, player_user, text: str) -> tuple[bool, str | None]:
    """DM a player from the club MTProto account (best-effort)."""

    body = (text or "").strip()
    if not body:
        return False, "empty_message"

    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                return False, "not_authorized"
            await client.send_message(player_user, body)
            return True, None
        except Exception as e:
            logger.warning(
                "send_player_dm_via_club failed club=%s: %s",
                cfg.club_key,
                type(e).__name__,
            )
            return False, type(e).__name__
        finally:
            await client.disconnect()


async def create_support_group(
    cfg: ClubGcConfig,
    *,
    bot_dm_username: str | None,
    player_user=None,
    link_join_client: TelegramClient | None = None,
) -> MtProtoGroupOutcome:
    """
    Create a basic Telegram group for ``cfg`` via MTProto, invite users + bot,
    optional photo + inner message.

    When ``player_user`` is set (Telethon User), that account is seeded into the
  new group at creation time (best-effort).

    Caller must ensure session is authenticated.
    """

    added_ok: list[dict] = []
    failed_ok: list[dict] = []
    warnings_local: list[str] = []

    photo_attempted = bool(cfg.group_photo_path)
    photo_ok = False
    initial_sent = False
    invite_link: str | None = None
    chat_id_big: int | None = None
    title_for_group = build_support_megagroup_title(cfg, player_user)
    title_out = title_for_group
    player_direct_add_ok: bool | None = None

    bot_label = cfg.bot_account or (f"@{bot_dm_username}" if bot_dm_username else None)
    creator_cfg = resolve_group_creator_cfg(cfg)
    link_join_cfg = resolve_link_join_cfg(cfg)
    promote_marker = (cfg.promote_admin_marker or "").strip() or None
    exclude_invites = link_join_exclude_normalized(cfg)

    link_joined_users: list[dict] = []
    promoted_admins: list[dict] = []
    link_join_failures: list[dict] = []
    group_ent = None

    raw_invites = list(get_gc_users_to_add(cfg))
    if bot_label:
        raw_invites.append(bot_label)
    else:
        warnings_local.append(
            "Skipping bot invite: bot has no username in Telegram and GC_BOT_ACCOUNT is unset."
        )

    invite_seen: set[str] = set()
    invite_targets: list[str] = []
    for raw in raw_invites:
        marker = raw.strip()
        if not marker:
            continue
        norm = marker.lower().lstrip("@")
        if norm in exclude_invites:
            continue
        if norm in invite_seen:
            continue
        invite_seen.add(norm)
        invite_targets.append(marker)

    async with get_mtproto_lock(creator_cfg.club_key):
        client = make_client(creator_cfg)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError(
                    "MTProto session is not authenticated; reply with your login steps from /gc."
                )

            seed_user = await _resolve_seed_user_for_create_chat(
                client,
                player_user=player_user,
                invite_targets=invite_targets,
            )
            if seed_user is None:
                raise RuntimeError(
                    "Cannot create a support group: Telegram requires at least one user to "
                    "invite. Add GC_USERS_* / GC_BOT_ACCOUNT or use /gc @player."
                )

            created = await _with_single_flood_retry(
                "CreateChatRequest",
                lambda: client(
                    CreateChatRequest(
                        users=[seed_user],
                        title=title_for_group,
                    )
                ),
            )

            chat = created.chats[0] if getattr(created, "chats", None) else None
            if not chat:
                raise RuntimeError("CreateChat succeeded but returned no chat.")

            group_ent = await client.get_entity(chat)

            try:
                from telethon.utils import get_peer_id

                chat_id_big = int(get_peer_id(group_ent))
            except Exception:
                chat_id_big = None

            title_attr = getattr(group_ent, "title", None) or getattr(chat, "title", None)
            if isinstance(title_attr, str) and title_attr.strip():
                title_out = title_attr.strip()

            if chat_id_big is not None:
                try:
                    from bot.handlers.groups import _mark_post_gc_bundle_window

                    _mark_post_gc_bundle_window(chat_id_big)
                except Exception as e:
                    logger.warning(
                        "post_gc suppression window mark failed chat_id=%s: %s",
                        chat_id_big,
                        type(e).__name__,
                    )

            if player_user is not None:
                player_direct_add_ok = False
                pm = _player_log_marker(player_user)
                seed_was_player = int(getattr(seed_user, "id", 0)) == int(
                    getattr(player_user, "id", 0)
                )
                if seed_was_player:
                    player_direct_add_ok = True
                    added_ok.append({"user": pm, "kind": "player"})
                else:
                    ok_ent, err_ent = await _invite_user_entity(
                        client, group_ent, player_user
                    )
                    if ok_ent:
                        player_direct_add_ok = True
                        added_ok.append({"user": pm, "kind": "player"})
                    else:
                        failed_ok.append(
                            {"user": pm, "reason": err_ent or "unknown", "kind": "player"}
                        )

            for marker in invite_targets:
                if player_user is not None and _marker_matches_user(marker, player_user):
                    continue
                if _marker_matches_user(marker, seed_user):
                    continue

                ok, err = await _invite_one(client, group_ent, marker)

                bn = bot_label or ""
                kind = (
                    "bot"
                    if bn
                    and marker.lower().strip().lstrip("@")
                    == bn.lower().strip().lstrip("@")
                    else "user"
                )
                if ok:
                    added_ok.append({"user": marker, "kind": kind})
                else:
                    failed_ok.append({"user": marker, "reason": err or "unknown", "kind": kind})

            photo_ok = await apply_club_group_photo(
                client,
                group_ent,
                cfg,
                warnings=warnings_local,
                failed_users=failed_ok,
            )

            try:
                invite_link = await _export_invite_link(client, group_ent)
                invite_link = normalize_invite_link(invite_link)
            except Exception as e:

                warnings_local.append(f"invite export failed: {type(e).__name__}")
                failed_ok.append(
                    {"user": "__invite_link__", "reason": repr(e)[1:200], "kind": "invite"}
                )

            tmpl = cfg.initial_group_message_template
            link_for_tpl = (
                invite_link.strip()
                if (invite_link and invite_link.strip())
                else "(invite link unavailable)"
            )
            inner = tmpl.format(invite_link=link_for_tpl, group_title=title_out)
            try:
                await _with_single_flood_retry(
                    "send_message(inner)",
                    lambda: client.send_message(group_ent, inner),
                )
                initial_sent = True
            except Exception as e:

                warnings_local.append(f"failed to send inner message ({type(e).__name__})")
                failed_ok.append(
                    {
                        "user": "__inner_message__",
                        "reason": repr(e)[1:200],
                        "kind": "message",
                    }
                )

        finally:
            await client.disconnect()

    if (
        link_join_cfg is not None
        and promote_marker
        and invite_link
        and group_ent is not None
    ):
        from bot.services.mtproto_group_join import run_link_join_and_promote

        lj, prom, fail = await run_link_join_and_promote(
            creator_cfg,
            group_entity=group_ent,
            invite_link=invite_link,
            promote_marker=promote_marker,
            link_join_cfg=link_join_cfg,
            link_join_client=link_join_client,
        )
        link_joined_users.extend(lj)
        promoted_admins.extend(prom)
        link_join_failures.extend(fail)

    group_ok_out = chat_id_big is not None
    ghint = None if chat_id_big is not None else "missing Telegram chat id after creation"
    group_photo_final = (not cfg.group_photo_path) or photo_ok

    return MtProtoGroupOutcome(
        ok=group_ok_out,
        telegram_chat_id=chat_id_big,
        telegram_chat_title=title_out,
        invite_link=invite_link,
        added_users=added_ok,
        failed_users=failed_ok,
        initial_message_sent=initial_sent,
        group_photo_attempted=photo_attempted,
        group_photo_ok=group_photo_final,
        warnings=warnings_local,
        error_hint=ghint,
        player_direct_add_ok=player_direct_add_ok,
        link_joined_users=link_joined_users,
        promoted_admins=promoted_admins,
        link_join_failures=link_join_failures,
    )


async def create_support_megagroup(
    cfg: ClubGcConfig,
    *,
    bot_dm_username: str | None,
    player_user=None,
    link_join_client: TelegramClient | None = None,
) -> MtProtoGroupOutcome:
    """Deprecated alias for :func:`create_support_group`."""

    return await create_support_group(
        cfg,
        bot_dm_username=bot_dm_username,
        player_user=player_user,
        link_join_client=link_join_client,
    )
