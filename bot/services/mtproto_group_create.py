"""MTProto (Telethon) helpers for `/gc`: auth, megagroup creation, invites, photo, invite link."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError, SessionPasswordNeededError
from telethon.tl.functions.channels import CreateChannelRequest, InviteToChannelRequest

from club_gc_settings import ClubGcConfig, get_tg_mtproto_credentials


logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
FLOODWAIT_MAX_SECONDS = 120

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


def make_client(cfg: ClubGcConfig) -> TelegramClient:
    api_id, api_hash = get_tg_mtproto_credentials()
    resolved = resolve_repo_path(cfg.mtproto_session)
    if resolved.suffix == ".session":
        stem = resolved.with_suffix("")
    else:
        stem = resolved
    stem.resolve().parent.mkdir(parents=True, exist_ok=True)
    session_arg = stem.as_posix()
    return TelegramClient(session_arg, api_id=api_id, api_hash=api_hash)


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
        client = make_client(cfg)
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
        client = make_client(cfg)
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
        client = make_client(cfg)
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


@dataclass
class MtProtoGroupOutcome:
    """Result of megagroup creation + post-setup."""

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


async def _invite_one(
    client: TelegramClient, channel_entity, marker: str
) -> tuple[bool, str | None]:
    try:
        ent = await _with_single_flood_retry(
            f"get_entity:{marker}",
            lambda: client.get_entity(marker.strip()),
        )
        if not getattr(ent, "access_hash", None):
            return False, "missing access_hash"

        await _with_single_flood_retry(
            f"invite:{marker}",
            lambda: client(
                InviteToChannelRequest(channel=channel_entity, users=[ent])
            ),
        )
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
        logger.info("Invite skipped for %s: %s", marker, type(e).__name__)
        return False, readable


async def create_support_megagroup(
    cfg: ClubGcConfig,
    *,
    bot_dm_username: str | None,
) -> MtProtoGroupOutcome:
    """
    Create megagroup for ``cfg`` via MTProto, invite users + bot, optional photo + inner message.

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
    title_out = cfg.group_title

    bot_label = cfg.bot_account or (f"@{bot_dm_username}" if bot_dm_username else None)

    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError(
                    "MTProto session is not authenticated; reply with your login steps from /gc."
                )

            mega = await _with_single_flood_retry(
                "CreateChannelRequest",
                lambda: client(
                    CreateChannelRequest(
                        title=cfg.group_title,
                        about="Support group",
                        megagroup=True,
                        broadcast=False,
                    )
                ),
            )

            chan = mega.chats[0] if getattr(mega, "chats", None) else None
            if not chan:
                raise RuntimeError("CreateChannel succeeded but returned no channel.")

            channel_ent = await client.get_entity(chan)
            if not getattr(channel_ent, "access_hash", None):
                raise RuntimeError("Created channel lacks access_hash; cannot finalize setup.")

            try:
                from telethon.utils import get_peer_id

                chat_id_big = int(get_peer_id(channel_ent))
            except Exception:
                chat_id_big = None

            title_attr = getattr(channel_ent, "title", None) or getattr(chan, "title", None)
            if isinstance(title_attr, str) and title_attr.strip():
                title_out = title_attr.strip()

            invite_targets = list(cfg.users_to_add)
            if bot_label:
                invite_targets.append(bot_label)
            else:
                warnings_local.append(
                    "Skipping bot invite: bot has no username in Telegram and GC_BOT_ACCOUNT is unset."
                )

            for raw in invite_targets:
                marker = raw.strip()
                if not marker:
                    continue

                ok, err = await _invite_one(client, channel_ent, marker)

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

            if cfg.group_photo_path:
                photo_abs = resolve_repo_path(cfg.group_photo_path)
                if photo_abs.exists():
                    try:
                        edit_fn = getattr(client, "edit_photo", None)
                        if callable(edit_fn):

                            async def upload_edit():
                                return await edit_fn(channel_ent, file=photo_abs.as_posix())

                            await _with_single_flood_retry("edit_photo", upload_edit)
                            photo_ok = True
                        else:

                            warnings_local.append("Telethon client has no edit_photo; skipped photo.")

                    except Exception as e:

                        warnings_local.append(f"group photo upload failed ({type(e).__name__})")
                        failed_ok.append(
                            {"user": "__group_photo__", "reason": type(e).__name__, "kind": "photo"}
                        )
                else:
                    warnings_local.append(
                        f"group photo path missing on disk: {photo_abs.as_posix()} — skipped."
                    )

            try:
                invite_link = await _export_invite_link(client, channel_ent)
                invite_link = normalize_invite_link(invite_link)
            except Exception as e:

                warnings_local.append(f"invite export failed: {type(e).__name__}")
                failed_ok.append(
                    {"user": "__invite_link__", "reason": repr(e)[1:200], "kind": "invite"}
                )

            if invite_link:
                tmpl = cfg.initial_group_message_template
                inner = tmpl.format(invite_link=invite_link, group_title=title_out)
                try:
                    await _with_single_flood_retry(
                        "send_message(inner)",
                        lambda: client.send_message(channel_ent, inner),
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

            mega_ok_out = chat_id_big is not None
            ghint = None
            if chat_id_big is None:
                ghint = "missing Telegram chat id after creation"

            group_photo_final = (not cfg.group_photo_path) or photo_ok

            return MtProtoGroupOutcome(
                ok=mega_ok_out,
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
            )
        finally:
            await client.disconnect()
