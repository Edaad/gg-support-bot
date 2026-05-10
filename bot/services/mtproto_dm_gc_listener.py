"""Telethon listeners: outgoing /gc in admin→player DMs creates or reuses support megagroups."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from telethon import TelegramClient, events
from telethon.tl.types import PeerUser, User

from club_gc_settings import (
    CLUB_GC_CONFIG,
    get_tg_mtproto_credentials,
    is_dm_gc_listener_enabled,
    is_dm_gc_verbose_logging,
)
from bot.handlers.groups import send_post_gc_intro_bundle
from bot.services.club import ensure_group_chat_linked
from bot.services.mtproto_group_create import (
    create_support_megagroup,
    ensure_player_in_support_group,
    export_invite_link_for_peer,
    is_client_authorized,
    make_client,
)
from bot.services.player_support_dm_messages import (
    PLAYER_ADDED_SUCCESS_MESSAGE,
    PLAYER_EXISTING_GROUP_MESSAGE,
    PLAYER_EXISTING_INVITE_MESSAGE,
    PLAYER_INVITE_FALLBACK_MESSAGE,
    PLAYER_RE_ADDED_MESSAGE,
)
from bot.services.support_group_chats import (
    fetch_support_group_chat_by_club_player,
    persist_support_group_chat_row,
    pg_advisory_unlock_session,
    try_pg_advisory_lock_club_player,
    update_support_group_chat_row,
)

logger = logging.getLogger(__name__)

_clients: list[TelegramClient] = []


def _dm_gc_verbose_info(msg: str, *args) -> None:
    if is_dm_gc_verbose_logging():
        logger.info(msg, *args)
_loop_holder: dict[str, Any] = {}


def _telethon_user_label(ent: Any) -> str:
    """Readable label for Telegram user / Telethon ``get_me()`` (for operator logs)."""
    if ent is None:
        return "(?)"
    uid = getattr(ent, "id", None)
    un = getattr(ent, "username", None)
    if isinstance(un, str) and un.strip():
        handle = un.strip().lstrip("@")
        return f"@{handle} [id={uid}]"
    fn = (getattr(ent, "first_name", None) or "").strip()
    ln = (getattr(ent, "last_name", None) or "").strip()
    name = f"{fn} {ln}".strip()
    if name:
        return f"{name} [id={uid}]"
    return f"user[id={uid}]"


async def _send_player_dm_safe(client: TelegramClient, player: User, text: str) -> tuple[bool, str | None]:
    try:
        await client.send_message(player, text)
        return True, None
    except Exception as e:
        logger.warning("dm_gc player DM failed: %s", type(e).__name__)
        return False, type(e).__name__


async def _flow_existing_group(
    client: TelegramClient,
    cfg,
    row,
    player: User,
    *,
    listener_label: str,
) -> None:
    try:
        channel = await client.get_entity(row.telegram_chat_id)
    except Exception as e:
        logger.warning(
            "dm_gc /gc failed: cannot_load_existing_megagroup club_key=%s listener=%s "
            "telegram_chat_id=%s err=%s",
            cfg.club_key,
            listener_label,
            row.telegram_chat_id,
            type(e).__name__,
        )
        return

    st = await ensure_player_in_support_group(client, channel, player)
    exported = await export_invite_link_for_peer(client, channel)
    new_link = exported or row.invite_link
    link = (new_link or "").strip()

    if st == "already_member":
        dm_body = PLAYER_EXISTING_GROUP_MESSAGE
        dm_status = "existing_member"
    elif st == "invited_ok":
        dm_body = PLAYER_RE_ADDED_MESSAGE
        dm_status = "re_added"
    else:
        dm_body = PLAYER_EXISTING_INVITE_MESSAGE.format(
            invite_link=link or "(invite link unavailable)"
        )
        dm_status = "existing_invite_fallback"

    dm_ok, dm_err = await _send_player_dm_safe(client, player, dm_body)
    player_label_existing = _telethon_user_label(player)
    if not dm_ok:
        logger.warning(
            "dm_gc /gc player_dm_issue club_key=%s listener=%s player=%s err=%s template=%s(flow=existing)",
            cfg.club_key,
            listener_label,
            player_label_existing,
            dm_err,
            dm_status,
        )

    uname = player.username.strip() if player.username else None
    dname = (f"{player.first_name or ''} {player.last_name or ''}").strip() or None
    err_extra = f"player_dm:{dm_err}" if (not dm_ok and dm_err) else None

    update_support_group_chat_row(
        row.id,
        invite_link=new_link,
        player_username=uname,
        player_display_name=dname,
        player_dm_status=dm_status + ("_dm_failed" if not dm_ok else ""),
        last_error_message=err_extra if err_extra else "",
    )
    _dm_gc_verbose_info(
        "dm_gc /gc finished existing_support_group club_key=%s listener=%s player=%s status=%s",
        cfg.club_key,
        listener_label,
        player_label_existing,
        dm_status,
    )


async def _flow_new_group(
    client: TelegramClient,
    cfg,
    player: User,
    bot_dm_username: str | None,
    ptb_bot,
    *,
    listener_label: str,
) -> None:
    me = await client.get_me()
    admin_id = me.id
    uname = player.username.strip() if player.username else None
    dname = (f"{player.first_name or ''} {player.last_name or ''}").strip() or None
    player_label = _telethon_user_label(player)

    try:
        outcome = await create_support_megagroup(
            cfg, bot_dm_username=bot_dm_username, player_user=player
        )
    except Exception as e:
        logger.exception(
            "dm_gc /gc failed: create_support_megagroup threw club_key=%s listener=%s player=%s: %s",
            cfg.club_key,
            listener_label,
            player_label,
            type(e).__name__,
        )
        return

    cid = outcome.telegram_chat_id
    if cid is None:
        warn_tail = "; ".join((outcome.warnings or [])[:8]) if outcome.warnings else ""
        logger.warning(
            "dm_gc /gc failed: megagroup_missing_chat_id club_key=%s listener=%s player=%s "
            "error_hint=%s warnings_preview=%s",
            cfg.club_key,
            listener_label,
            player_label,
            outcome.error_hint or "(none)",
            warn_tail[:500] if warn_tail else "(none)",
        )
        return

    link = (outcome.invite_link or "").strip()
    if outcome.player_direct_add_ok:
        dm_body = PLAYER_ADDED_SUCCESS_MESSAGE
        dm_status = "player_added_success"
    else:
        dm_body = PLAYER_INVITE_FALLBACK_MESSAGE.format(
            invite_link=link or "(invite link unavailable)"
        )
        dm_status = "player_invite_fallback"

    dm_ok, dm_err = await _send_player_dm_safe(client, player, dm_body)
    if not dm_ok:
        logger.warning(
            "dm_gc /gc player_dm_issue club_key=%s listener=%s player=%s err=%s template=%s",
            cfg.club_key,
            listener_label,
            player_label,
            dm_err,
            dm_status,
        )

    errs = list(outcome.warnings)
    if outcome.error_hint:
        errs.append(outcome.error_hint)
    if not dm_ok and dm_err:
        errs.append(f"player_dm:{dm_err}")
    last_err = "; ".join(errs) if errs else None

    pk, perr = persist_support_group_chat_row(
        club_key=cfg.club_key,
        club_display_name=cfg.club_display_name,
        telegram_chat_id=cid,
        telegram_chat_title=outcome.telegram_chat_title,
        invite_link=outcome.invite_link,
        created_by_telegram_user_id=admin_id,
        mtproto_session_name=cfg.mtproto_session,
        added_users=outcome.added_users,
        failed_users=outcome.failed_users,
        group_photo_path=cfg.group_photo_path,
        initial_group_message_sent=outcome.initial_message_sent,
        last_error_message=last_err,
        player_telegram_user_id=player.id,
        player_username=uname,
        player_display_name=dname,
        player_dm_status=dm_status + ("_dm_failed" if not dm_ok else ""),
    )

    if perr == "duplicate_club_player":
        _dm_gc_verbose_info(
            "dm_gc /gc raced duplicate DB row club_key=%s listener=%s player=%s — running existing-group flow",
            cfg.club_key,
            listener_label,
            player_label,
        )
        existing = fetch_support_group_chat_by_club_player(cfg.club_key, player.id)
        if existing:
            await _flow_existing_group(
                client, cfg, existing, player, listener_label=listener_label
            )
        return

    if pk is None:
        logger.warning(
            "dm_gc /gc failed: support_group_chat_row_not_saved club_key=%s listener=%s "
            "player=%s persist_err=%s",
            cfg.club_key,
            listener_label,
            player_label,
            perr or "unknown",
        )
        return

    linked = ensure_group_chat_linked(cid, cfg.link_club_id, outcome.telegram_chat_title)
    if not linked:
        logger.warning(
            "dm_gc /gc failed: dashboard_group_link club_key=%s listener=%s chat_id=%s link_club_id=%s",
            cfg.club_key,
            listener_label,
            cid,
            cfg.link_club_id,
        )
    else:
        try:
            await send_post_gc_intro_bundle(
                ptb_bot, cid, cfg.link_club_id, outcome.telegram_chat_title
            )
        except Exception as e:
            logger.exception(
                "dm_gc /gc failed: post_intro_bundle club_key=%s listener=%s chat_id=%s: %s",
                cfg.club_key,
                listener_label,
                cid,
                type(e).__name__,
            )
    _dm_gc_verbose_info(
        "dm_gc /gc finished new_support_group club_key=%s listener=%s player=%s chat_id=%s row_id=%s",
        cfg.club_key,
        listener_label,
        player_label,
        cid,
        pk,
    )


async def handle_dm_gc_message(
    event,
    cfg,
    bot_dm_username: str | None,
    ptb_bot,
    *,
    listener_label: str,
) -> None:
    if not event.is_private:
        return
    if not isinstance(event.peer_id, PeerUser):
        return

    msg = getattr(event, "message", None)
    body_raw = event.raw_text
    if not body_raw and msg is not None:
        attr = getattr(msg, "message", None)
        body_raw = attr if isinstance(attr, str) else ""
    body_raw = body_raw if isinstance(body_raw, str) else ""
    trimmed = body_raw.strip()
    gc_match = trimmed == "/gc"

    peer_user_id = getattr(event.peer_id, "user_id", None)
    msg_id = getattr(msg, "id", None)
    snippet = trimmed[:400] + ("..." if len(trimmed) > 400 else "")
    _dm_gc_verbose_info(
        "dm_gc dm_capture club_key=%s listener=%s peer_user_id=%s message_id=%s "
        "message=%r /gc_match=%s",
        cfg.club_key,
        listener_label,
        peer_user_id,
        msg_id,
        snippet,
        gc_match,
    )

    text = trimmed
    if text != "/gc":
        return

    try:
        chat = await event.get_chat()
    except Exception as e:
        logger.warning(
            "dm_gc /gc failed: cannot_resolve_dm_peer club_key=%s listener=%s err=%s",
            cfg.club_key,
            listener_label,
            type(e).__name__,
        )
        return

    if not isinstance(chat, User):
        logger.warning(
            "dm_gc /gc ignored: peer_not_user club_key=%s listener=%s peer_type=%s",
            cfg.club_key,
            listener_label,
            type(chat).__name__,
        )
        return
    if getattr(chat, "bot", False):
        logger.warning(
            "dm_gc /gc ignored: peer_is_bot club_key=%s listener=%s",
            cfg.club_key,
            listener_label,
        )
        return

    player = chat
    player_id = player.id
    player_label = _telethon_user_label(player)

    _dm_gc_verbose_info(
        "dm_gc sensed outgoing /gc club_key=%s listener_account=%s player=%s",
        cfg.club_key,
        listener_label,
        player_label,
    )

    try:
        await event.delete()
    except Exception as e:
        logger.warning(
            "dm_gc delete /gc failed (continuing): club_key=%s listener=%s err=%s",
            cfg.club_key,
            listener_label,
            type(e).__name__,
        )

    lock_sess, acquired = try_pg_advisory_lock_club_player(cfg.club_key, player_id)
    if not acquired:
        logger.warning(
            "dm_gc /gc failed: advisory_lock_busy club_key=%s listener=%s player=%s "
            "(another worker may hold lock)",
            cfg.club_key,
            listener_label,
            player_label,
        )
        return

    try:
        client = event.client
        existing = fetch_support_group_chat_by_club_player(cfg.club_key, player_id)
        if existing:
            await _flow_existing_group(
                client, cfg, existing, player, listener_label=listener_label
            )
        else:
            await _flow_new_group(
                client,
                cfg,
                player,
                bot_dm_username,
                ptb_bot,
                listener_label=listener_label,
            )
    except Exception as e:
        logger.exception(
            "dm_gc /gc failed: unexpected handler_error club_key=%s listener=%s player=%s: %s",
            cfg.club_key,
            listener_label,
            player_label,
            type(e).__name__,
        )
    finally:
        pg_advisory_unlock_session(lock_sess, cfg.club_key, player_id)


async def _async_main(bot_token: str) -> None:
    global _clients

    try:
        get_tg_mtproto_credentials()
    except RuntimeError as e:
        logger.error("dm_gc listener: %s", e)
        return

    if not is_dm_gc_listener_enabled():
        return

    from telegram import Bot

    ptb_bot = Bot(bot_token)
    await ptb_bot.initialize()
    try:
        me = await ptb_bot.get_me()
        bot_dm_username = me.username.strip() if me.username else None
    except Exception as e:
        logger.exception("dm_gc Bot.get_me failed: %s", type(e).__name__)
        await ptb_bot.shutdown()
        return

    started: list[TelegramClient] = []

    for cfg in CLUB_GC_CONFIG.values():
        if not await is_client_authorized(cfg):
            logger.warning(
                "dm_gc skip club_key=%s: Telethon session not authorized — outgoing /gc in admin→player DM "
                "will not trigger for this club until Dashboard Telegram login (or CLI) completes.",
                cfg.club_key,
            )
            continue

        client = make_client(cfg)
        await client.connect()
        if not await client.is_user_authorized():
            logger.warning(
                "dm_gc skip club_key=%s: not authorized after connect (check session file / Postgres StringSession)",
                cfg.club_key,
            )
            await client.disconnect()
            continue

        me_who = await client.get_me()
        listener_label = _telethon_user_label(me_who)
        _dm_gc_verbose_info(
            "dm_gc listening for outgoing /gc club_key=%s listener_account=%s telegram_user_id=%s",
            cfg.club_key,
            listener_label,
            getattr(me_who, "id", "?"),
        )

        def _make_dm_gc_handler(label: str, club_cfg_inner):
            async def _handler(event):
                await handle_dm_gc_message(
                    event,
                    club_cfg_inner,
                    bot_dm_username,
                    ptb_bot,
                    listener_label=label,
                )

            return _handler

        client.add_event_handler(
            _make_dm_gc_handler(listener_label, cfg),
            events.NewMessage(outgoing=True),
        )
        started.append(client)

    _clients[:] = started
    _loop_holder["ptb_bot"] = ptb_bot

    _dm_gc_verbose_info(
        "dm_gc listener bootstrap complete telethon_sessions=%s for_outgoing_dm_gc",
        len(started),
    )

    if not started:
        logger.warning("dm_gc no Telethon clients started")
        await ptb_bot.shutdown()
        return

    try:
        await asyncio.gather(*(c.run_until_disconnected() for c in started))
    finally:
        for c in started:
            try:
                if c.is_connected():
                    await c.disconnect()
            except Exception:
                pass
        try:
            await ptb_bot.shutdown()
        except Exception:
            pass
        _clients.clear()
        _loop_holder.pop("ptb_bot", None)


def start_listener_background(bot_token: str) -> None:
    if not is_dm_gc_listener_enabled():
        _dm_gc_verbose_info("dm_gc listener disabled (GC_DM_GC_LISTENER_ENABLED is false/off)")
        return

    def runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _loop_holder["loop"] = loop
        try:
            loop.run_until_complete(_async_main(bot_token))
        finally:
            loop.close()
            _loop_holder.pop("loop", None)

    threading.Thread(target=runner, daemon=True, name="mtproto-dm-gc").start()
    _dm_gc_verbose_info("dm_gc listener thread started")


def stop_listener_background() -> None:
    loop = _loop_holder.get("loop")
    if not loop or not loop.is_running():
        return

    async def _disconnect_all():
        for c in list(_clients):
            try:
                if c.is_connected():
                    await c.disconnect()
            except Exception:
                pass
        p = _loop_holder.get("ptb_bot")
        if p:
            try:
                await p.shutdown()
            except Exception:
                pass

    fut = asyncio.run_coroutine_threadsafe(_disconnect_all(), loop)
    try:
        fut.result(timeout=20)
    except Exception as e:
        logger.warning("dm_gc stop: %s", type(e).__name__)
