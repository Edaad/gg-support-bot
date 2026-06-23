"""MTProto best-effort: save group chat title as Telegram contact first name for the sole player."""

from __future__ import annotations

import asyncio
import logging
import time

from telegram import Bot

from club_gc_settings import (
    ClubGcConfig,
    get_club_gc_config_by_link_club_id,
    is_contact_save_enabled,
)
from bot.services.mtproto_group_create import (
    _with_single_flood_retry,
    get_mtproto_lock,
    is_client_authorized,
    make_client,
)
from bot.services.mtproto_group_player import find_sole_player_participant

logger = logging.getLogger(__name__)

# Telegram contact first name conservative cap (characters).
_CONTACT_FIRST_MAX = 64

_notify_bot: Bot | None = None
_channels_too_much_last_notify: dict[str, float] = {}
_CHANNELS_TOO_MUCH_NOTIFY_COOLDOWN_SEC = 600.0
_mtproto_disconnect_last_notify: dict[str, float] = {}
_MTPROTO_DISCONNECT_NOTIFY_COOLDOWN_SEC = 600.0


def set_contact_save_notify_bot(bot: Bot | None) -> None:
    """Called from bot ``post_init`` so contact-save failures can DM the club GC admin."""

    global _notify_bot
    _notify_bot = bot


def schedule_save_player_contact_named_group(
    *,
    chat_id: int,
    club_id: int | None,
    chat_title: str | None,
) -> None:
    """Fire-and-forget; never raises. Triggers: group title change, ``/track``, ``/info``."""
    if not is_contact_save_enabled():
        return
    if club_id is None:
        return
    title_t = (chat_title or "").strip()
    if not title_t:
        return

    cfg = get_club_gc_config_by_link_club_id(int(club_id))
    if not cfg:
        logger.debug(
            "contact_save: no ClubGcConfig for dashboard club_id=%s", club_id
        )
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("contact_save: no running event loop chat_id=%s", chat_id)
        return

    async def _run_wrapped():
        try:
            note = await _maybe_save_player_contact(
                chat_id=chat_id, cfg=cfg, chat_title=title_t
            )
            if note:
                await _notify_club_gc_admin_dm(cfg, chat_id, note)
        except Exception:
            logger.exception(
                "contact_save: unexpected error chat_id=%s club=%s",
                chat_id,
                cfg.club_key,
            )
            await _notify_club_gc_admin_dm(
                cfg,
                chat_id,
                "Contact save crashed (see worker logs).",
            )

    loop.create_task(_run_wrapped(), name=f"contact-save-{chat_id}")


async def notify_club_gc_admin_dm(
    cfg: ClubGcConfig,
    text: str,
    *,
    parse_mode: str | None = None,
) -> None:
    """DM the club GC admin via the GG Support bot (admin must have /start'd the bot)."""

    bot = _notify_bot
    if not bot:
        return
    body = (text or "").strip()
    if not body:
        return
    kwargs: dict = {"chat_id": cfg.command_admin_user_id, "text": body[:4096]}
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    try:
        await bot.send_message(**kwargs)
    except Exception:
        logger.debug(
            "gc_admin_dm: notify failed club=%s admin_user_id=%s",
            cfg.club_key,
            cfg.command_admin_user_id,
            exc_info=True,
        )


async def notify_club_gc_channels_too_much(
    cfg: ClubGcConfig,
    *,
    player_label: str,
    trigger: str | None = None,
) -> None:
    """DM club GC admin when megagroup creation hits Telegram's group/channel cap."""

    now = time.monotonic()
    last = _channels_too_much_last_notify.get(cfg.club_key, 0.0)
    if now - last < _CHANNELS_TOO_MUCH_NOTIFY_COOLDOWN_SEC:
        return
    _channels_too_much_last_notify[cfg.club_key] = now

    lines = [
        f"⚠️ {cfg.club_display_name}: cannot create a support group — the club "
        "MTProto account has joined too many Telegram groups/channels "
        "(ChannelsTooMuchError).",
        f"Player: {player_label}",
    ]
    if trigger:
        lines.append(f"Trigger: {trigger}")
    lines.append(
        "Leave inactive support groups on the club account, then retry /gc or have "
        "the player DM again."
    )
    await notify_club_gc_admin_dm(cfg, "\n".join(lines))


async def notify_club_gc_mtproto_disconnected(
    cfg: ClubGcConfig,
    *,
    status_detail: str | None = None,
) -> None:
    """DM club GC admin when the worker loses this club's MTProto session."""

    now = time.monotonic()
    last = _mtproto_disconnect_last_notify.get(cfg.club_key, 0.0)
    if now - last < _MTPROTO_DISCONNECT_NOTIFY_COOLDOWN_SEC:
        return
    _mtproto_disconnect_last_notify[cfg.club_key] = now

    lines = [
        f"⚠️ {cfg.club_display_name}: MTProto is not connected on the support bot server.",
        "",
        "There is an issue with the server — please inform a head admin or engineer.",
        "",
        "Until this is resolved, /add, /cash, and automatic /gc (when a player DMs "
        "the club account) will not work.",
    ]
    detail = (status_detail or "").strip()
    if detail:
        lines.extend(["", f"Detail: {detail}"])
    await notify_club_gc_admin_dm(cfg, "\n".join(lines))


def clear_mtproto_disconnect_notify_cooldown(club_key: str) -> None:
    """Allow a fresh disconnect DM after the club session reconnects."""

    _mtproto_disconnect_last_notify.pop(club_key, None)


async def notify_all_gc_admins_dm(
    text: str,
    *,
    parse_mode: str | None = None,
) -> None:
    """DM each club GC admin (deduped by Telegram user id)."""

    from club_gc_settings import CLUB_GC_CONFIG

    body = (text or "").strip()
    if not body:
        return
    seen: set[int] = set()
    for cfg in CLUB_GC_CONFIG.values():
        admin_id = int(cfg.command_admin_user_id)
        if admin_id in seen:
            continue
        seen.add(admin_id)
        await notify_club_gc_admin_dm(cfg, body, parse_mode=parse_mode)


async def notify_rt_support_admin_dm(text: str) -> None:
    """DM the Round Table GC admin (central ops) via the GG Support bot."""

    from club_gc_settings import CLUB_GC_CONFIG

    cfg = CLUB_GC_CONFIG.get("round_table")
    if cfg is None:
        return
    prefix = "[Migration recovery ops]"
    body = (text or "").strip()
    if not body:
        return
    await notify_club_gc_admin_dm(cfg, f"{prefix}\n{body}")


async def _notify_club_gc_admin_dm(
    cfg: ClubGcConfig, chat_id: int, reason: str
) -> None:
    await notify_club_gc_admin_dm(
        cfg, f"[{cfg.club_display_name}] Chat {chat_id}: {reason}"
    )


def _truncate_first_name(raw: str) -> str:
    s = raw.strip()
    if not s:
        return ""
    if len(s) <= _CONTACT_FIRST_MAX:
        return s
    return s[: _CONTACT_FIRST_MAX - 1].rstrip() + "…"


async def _maybe_save_player_contact(
    *, chat_id: int, cfg: ClubGcConfig, chat_title: str
) -> str | None:
    """Returns a short human reason for admin DM when save does not succeed; ``None`` on success."""

    try:
        from bot.services.mtproto_group_create import get_tg_mtproto_credentials

        get_tg_mtproto_credentials()
    except RuntimeError:
        logger.debug("contact_save: TG_API_ID/TG_API_HASH unset")
        return None

    fn = _truncate_first_name(chat_title)
    if not fn:
        return None

    if not await is_client_authorized(cfg):
        logger.info("contact_save: MTProto unauthorized club=%s", cfg.club_key)
        return "MTProto session not authorized (log in via Dashboard)."

    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg)
        await client.connect()
        try:
            try:
                me = await client.get_me()
                self_id = int(me.id) if me else None
            except Exception:
                self_id = None

            try:
                chan = await _with_single_flood_retry(
                    "get_entity_chat",
                    lambda: client.get_entity(chat_id),
                )
            except Exception as e:
                logger.warning(
                    "contact_save: get_entity failed chat_id=%s: %s",
                    chat_id,
                    type(e).__name__,
                )
                return f"Could not open chat ({type(e).__name__})."

            try:
                sole = await find_sole_player_participant(
                    client, chan, cfg, self_id=self_id
                )
            except Exception as e:
                logger.warning(
                    "contact_save: list participants chat_id=%s: %s",
                    chat_id,
                    type(e).__name__,
                )
                return f"Could not list members ({type(e).__name__})."

            if sole.candidate_count != 1 or sole.user is None:
                preview = ",".join(str(x) for x in sole.candidate_ids[:6])
                logger.warning(
                    "contact_save: skip chat_id=%s club=%s candidate_count=%s ids_sample=%s",
                    chat_id,
                    cfg.club_key,
                    sole.candidate_count,
                    preview,
                )
                return (
                    f"Contact not saved: need exactly one eligible player, "
                    f"found {sole.candidate_count}."
                )

            user_obj = sole.user
            from telethon.tl import functions

            try:
                inp = await client.get_input_entity(user_obj)
            except Exception as e:
                logger.warning(
                    "contact_save: get_input_entity failed: %s", type(e).__name__
                )
                return f"Could not resolve player ({type(e).__name__})."

            try:
                await _with_single_flood_retry(
                    "AddContactRequest",
                    lambda: client(
                        functions.contacts.AddContactRequest(
                            id=inp,
                            first_name=fn,
                            last_name="",
                            phone="",
                            add_phone_privacy_exception=False,
                        )
                    ),
                )
                logger.info(
                    "contact_save: saved club=%s player_user_id=%s",
                    cfg.club_key,
                    getattr(user_obj, "id", "?"),
                )
                return None
            except Exception as e:
                logger.warning(
                    "contact_save: AddContact failed club=%s: %s",
                    cfg.club_key,
                    type(e).__name__,
                )
                return f"AddContact failed ({type(e).__name__})."
        finally:
            await client.disconnect()
