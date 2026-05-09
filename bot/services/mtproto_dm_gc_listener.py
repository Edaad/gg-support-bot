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
_loop_holder: dict[str, Any] = {}


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
) -> None:
    try:
        channel = await client.get_entity(row.telegram_chat_id)
    except Exception as e:
        logger.warning("dm_gc get_entity channel: %s", type(e).__name__)
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


async def _flow_new_group(
    client: TelegramClient,
    cfg,
    player: User,
    bot_dm_username: str | None,
    ptb_bot,
) -> None:
    me = await client.get_me()
    admin_id = me.id
    uname = player.username.strip() if player.username else None
    dname = (f"{player.first_name or ''} {player.last_name or ''}").strip() or None

    try:
        outcome = await create_support_megagroup(
            cfg, bot_dm_username=bot_dm_username, player_user=player
        )
    except Exception as e:
        logger.exception("dm_gc create_support_megagroup failed: %s", type(e).__name__)
        return

    cid = outcome.telegram_chat_id
    if cid is None:
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
        existing = fetch_support_group_chat_by_club_player(cfg.club_key, player.id)
        if existing:
            await _flow_existing_group(client, cfg, existing, player)
        return

    if pk is None:
        return

    linked = ensure_group_chat_linked(cid, cfg.link_club_id, outcome.telegram_chat_title)
    if not linked:
        logger.warning(
            "dm_gc ensure_group_chat_linked failed chat_id=%s club_id=%s",
            cid,
            cfg.link_club_id,
        )
    else:
        try:
            await send_post_gc_intro_bundle(
                ptb_bot, cid, cfg.link_club_id, outcome.telegram_chat_title
            )
        except Exception as e:
            logger.exception("dm_gc send_post_gc_intro_bundle: %s", type(e).__name__)


async def handle_dm_gc_message(
    event,
    cfg,
    bot_dm_username: str | None,
    ptb_bot,
) -> None:
    if not event.is_private:
        return
    if not isinstance(event.peer_id, PeerUser):
        return

    text = (event.raw_text or "").strip()
    if text != "/gc":
        return

    try:
        chat = await event.get_chat()
    except Exception as e:
        logger.warning("dm_gc get_chat: %s", type(e).__name__)
        return

    if not isinstance(chat, User) or getattr(chat, "bot", False):
        return

    player = chat
    player_id = player.id

    try:
        await event.delete()
    except Exception as e:
        logger.warning("dm_gc delete /gc: %s", type(e).__name__)

    lock_sess, acquired = try_pg_advisory_lock_club_player(cfg.club_key, player_id)
    if not acquired:
        logger.info("dm_gc advisory lock busy club=%s player=%s", cfg.club_key, player_id)
        return

    try:
        client = event.client
        existing = fetch_support_group_chat_by_club_player(cfg.club_key, player_id)
        if existing:
            await _flow_existing_group(client, cfg, existing, player)
        else:
            await _flow_new_group(client, cfg, player, bot_dm_username, ptb_bot)
    except Exception as e:
        logger.exception("dm_gc handler error: %s", type(e).__name__)
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
            logger.warning("dm_gc skip club=%s (session not authorized)", cfg.club_key)
            continue

        client = make_client(cfg)
        await client.connect()
        if not await client.is_user_authorized():
            logger.warning("dm_gc skip club=%s (not authorized after connect)", cfg.club_key)
            await client.disconnect()
            continue

        def _register(c: TelegramClient, club_cfg):
            async def _handler(event):
                await handle_dm_gc_message(event, club_cfg, bot_dm_username, ptb_bot)

            c.add_event_handler(_handler, events.NewMessage(outgoing=True))

        _register(client, cfg)
        started.append(client)

    _clients[:] = started
    _loop_holder["ptb_bot"] = ptb_bot

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
        logger.info("dm_gc listener disabled (GC_DM_GC_LISTENER_ENABLED)")
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
    logger.info("dm_gc listener thread started")


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
