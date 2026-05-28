"""Telethon listeners: /gc on admin→player DMs (outgoing /gc) and on any incoming player DM."""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from telethon import TelegramClient, events
from telethon.tl.types import PeerUser, User

from club_gc_settings import (
    CLUB_GC_CONFIG,
    get_dm_gc_listener_restart_config,
    get_tg_mtproto_credentials,
    is_dm_gc_listener_enabled,
    is_dm_gc_verbose_logging,
)
from bot.handlers.groups import send_post_gc_intro_bundle
from bot.services.club import ensure_group_chat_linked
from bot.services.club import find_group_chat_id_by_name, get_group_title_for_chat
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
from bot.services.mtproto_group_add import handle_group_add_outgoing
from bot.services.mtproto_group_cash import handle_group_cash_outgoing
from bot.services.mtproto_group_delete import handle_group_delete_outgoing
from bot.services.support_group_chats import (
    fetch_support_group_chat_by_club_player,
    fetch_support_group_chat_by_telegram_chat_id,
    persist_support_group_chat_row,
    pg_advisory_unlock_session,
    try_pg_advisory_lock_club_player,
    bind_player_for_gc_reuse,
    update_support_group_chat_row,
)
from db.connection import get_db
from db.models import Club

logger = logging.getLogger(__name__)

_clients: list[TelegramClient] = []
_loop_holder: dict[str, Any] = {}
_listener_stop = threading.Event()
_listener_metrics: dict[str, Any] = {
    "restart_count": 0,
    "cycle": 0,
    "last_exit_at": None,
    "last_error": None,
    "last_disconnect_reason": None,
    "running": False,
}


def _dm_gc_verbose_info(msg: str, *args) -> None:
    if is_dm_gc_verbose_logging():
        logger.info(msg, *args)


def get_dm_gc_listener_status() -> dict[str, Any]:
    """Public snapshot of the background Telethon listener thread."""
    loop = _loop_holder.get("loop")
    connected = sum(1 for c in _clients if c.is_connected())
    return {
        "enabled": is_dm_gc_listener_enabled(),
        "loop_running": loop is not None and loop.is_running(),
        "connected_clients": connected,
        "total_clients": len(_clients),
        "listener_running": bool(_listener_metrics.get("running")),
        "restart_count": int(_listener_metrics.get("restart_count") or 0),
        "cycle": int(_listener_metrics.get("cycle") or 0),
        "last_exit_at": _listener_metrics.get("last_exit_at"),
        "last_error": _listener_metrics.get("last_error"),
        "last_disconnect_reason": _listener_metrics.get("last_disconnect_reason"),
    }


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


async def _run_gc_flow_for_player(
    event,
    cfg,
    player: User,
    bot_dm_username: str | None,
    ptb_bot,
    *,
    listener_label: str,
    trigger: str,
    delete_trigger_message: bool = False,
) -> None:
    """Create or reuse a support megagroup for ``player`` (shared by incoming DM and outgoing /gc)."""
    player_id = player.id
    player_label = _telethon_user_label(player)

    _dm_gc_verbose_info(
        "dm_gc sensed %s club_key=%s listener_account=%s player=%s",
        trigger,
        cfg.club_key,
        listener_label,
        player_label,
    )

    if delete_trigger_message:
        try:
            await event.delete()
        except Exception as e:
            logger.warning(
                "dm_gc delete trigger failed (continuing): club_key=%s listener=%s trigger=%s err=%s",
                cfg.club_key,
                listener_label,
                trigger,
                type(e).__name__,
            )

    lock_sess, acquired = try_pg_advisory_lock_club_player(cfg.club_key, player_id)
    if not acquired:
        logger.warning(
            "dm_gc /gc failed: advisory_lock_busy club_key=%s listener=%s player=%s trigger=%s "
            "(another worker may hold lock)",
            cfg.club_key,
            listener_label,
            player_label,
            trigger,
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
            "dm_gc /gc failed: unexpected handler_error club_key=%s listener=%s player=%s trigger=%s: %s",
            cfg.club_key,
            listener_label,
            player_label,
            trigger,
            type(e).__name__,
        )
    finally:
        pg_advisory_unlock_session(lock_sess, cfg.club_key, player_id)


def _parse_bind_target_title(text: str) -> str | None:
    t = (text or "").strip()
    if not t:
        return None
    if not t.lower().startswith("/bind"):
        return None
    rest = t[5:].strip()
    return rest or None


async def _run_bind_flow_for_player(
    *,
    event,
    cfg,
    player: User,
    target_title: str,
    listener_label: str,
    ptb_bot,
) -> None:
    """Bind player to a support megagroup by exact Telegram group title.

    Searches the club MTProto account's accessible dialogs for an exact title match.
    Binds only when exactly one match is found; otherwise notifies the club admin.
    """
    player_id = int(player.id)
    player_label = _telethon_user_label(player)

    lock_sess, acquired = try_pg_advisory_lock_club_player(cfg.club_key, player_id)
    if not acquired:
        logger.warning(
            "dm_gc /bind failed: advisory_lock_busy club_key=%s listener=%s player=%s",
            cfg.club_key,
            listener_label,
            player_label,
        )
        return

    try:
        desired = (target_title or "").strip()
        if not desired:
            return

        # Find exact title matches in accessible dialogs.
        matches: list[tuple[int, Any]] = []
        try:
            from telethon.utils import get_peer_id

            async for d in event.client.iter_dialogs():
                title = (getattr(d, "name", None) or "").strip()
                if title != desired:
                    continue
                ent = getattr(d, "entity", None)
                if ent is None:
                    continue
                try:
                    cid = int(get_peer_id(ent))
                except Exception:
                    continue
                matches.append((cid, ent))
                if len(matches) >= 20:
                    break
        except Exception:
            await _send_player_dm_safe(
                event.client,
                player,
                "Bind failed: could not search dialogs on the club account.",
            )
            return

        if len(matches) != 1:
            # Notify club admin via Bot API.
            admin_id = None
            try:
                with get_db() as session:
                    club = session.query(Club).filter(Club.id == int(cfg.link_club_id)).one_or_none()
                    if club:
                        admin_id = int(club.telegram_user_id)
            except Exception:
                admin_id = None

            if admin_id is not None and ptb_bot is not None:
                try:
                    await ptb_bot.send_message(
                        chat_id=admin_id,
                        text=(
                            f"/bind failed for club={cfg.club_display_name} ({cfg.club_key}).\n"
                            f"GC name: {desired}\n"
                            f"Matches found: {len(matches)}\n\n"
                            "Fix: rename the target GC to be unique, then run /bind again."
                        ),
                    )
                except Exception:
                    pass

            await _send_player_dm_safe(
                event.client,
                player,
                "Could not bind you to a group right now (group name was not unique).\n"
                "An agent will assist you shortly.",
            )
            return

        chat_id, channel = matches[0]

        exported = await export_invite_link_for_peer(event.client, channel)
        link = (exported or "").strip()
        if not link:
            # Fallback to last stored invite link if we have an audit row.
            existing_by_chat = fetch_support_group_chat_by_telegram_chat_id(int(chat_id))
            link = ((existing_by_chat.invite_link if existing_by_chat else "") or "").strip()

        # Bind the player to this group for /gc reuse.
        uname = player.username.strip() if player.username else None
        dname = (f"{player.first_name or ''} {player.last_name or ''}").strip() or None
        title_attr = getattr(channel, "title", None)
        entity_title = title_attr.strip() if isinstance(title_attr, str) and title_attr.strip() else None
        ensure_group_chat_linked(int(chat_id), int(cfg.link_club_id), entity_title or target_title)
        title_now, _ = get_group_title_for_chat(int(chat_id))
        status, row_id = bind_player_for_gc_reuse(
            club_key=cfg.club_key,
            club_display_name=cfg.club_display_name,
            telegram_chat_id=int(chat_id),
            telegram_chat_title=title_now or entity_title or target_title,
            player_telegram_user_id=player_id,
            player_username=uname,
            player_display_name=dname,
        )

        if row_id and link:
            update_support_group_chat_row(
                int(row_id),
                invite_link=link,
                player_dm_status=f"bind_cmd_{status}",
            )

        if not link:
            await _send_player_dm_safe(
                event.client,
                player,
                "Bound you to the group, but an invite link could not be exported.\n"
                "An agent may need to add you manually.",
            )
            return

        await _send_player_dm_safe(
            event.client,
            player,
            f"Please use this link to join the group:\n{link}\n\n"
            "Once joined, an agent will assist you shortly.",
        )
        _dm_gc_verbose_info(
            "dm_gc /bind ok club_key=%s listener=%s player=%s chat_id=%s status=%s",
            cfg.club_key,
            listener_label,
            player_label,
            chat_id,
            status,
        )
    finally:
        pg_advisory_unlock_session(lock_sess, cfg.club_key, player_id)


async def _resolve_dm_player(event) -> User | None:
    try:
        chat = await event.get_chat()
    except Exception:
        return None
    if not isinstance(chat, User):
        return None
    if getattr(chat, "bot", False):
        return None
    return chat


async def handle_dm_gc_incoming(
    event,
    cfg,
    bot_dm_username: str | None,
    ptb_bot,
    *,
    listener_label: str,
) -> None:
    """Incoming private DM to the club MTProto account — run /gc for the sender."""
    if not event.is_private or event.out:
        return
    if not isinstance(event.peer_id, PeerUser):
        return

    player = await _resolve_dm_player(event)
    if not player:
        return

    try:
        me = await event.client.get_me()
        if int(player.id) == int(me.id):
            return
    except Exception:
        pass

    msg = getattr(event, "message", None)
    body_raw = event.raw_text
    if not body_raw and msg is not None:
        attr = getattr(msg, "message", None)
        body_raw = attr if isinstance(attr, str) else ""
    snippet = ((body_raw if isinstance(body_raw, str) else "") or "").strip()[:400]
    _dm_gc_verbose_info(
        "dm_gc incoming_dm club_key=%s listener=%s player=%s message=%r",
        cfg.club_key,
        listener_label,
        _telethon_user_label(player),
        snippet + ("..." if len(snippet) >= 400 else ""),
    )

    await _run_gc_flow_for_player(
        event,
        cfg,
        player,
        bot_dm_username,
        ptb_bot,
        listener_label=listener_label,
        trigger="incoming_dm",
        delete_trigger_message=False,
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
    bind_title = _parse_bind_target_title(trimmed)

    peer_user_id = getattr(event.peer_id, "user_id", None)
    msg_id = getattr(msg, "id", None)
    snippet = trimmed[:400] + ("..." if len(trimmed) > 400 else "")
    _dm_gc_verbose_info(
        "dm_gc dm_capture club_key=%s listener=%s peer_user_id=%s message_id=%s "
        "message=%r /gc_match=%s /bind_match=%s",
        cfg.club_key,
        listener_label,
        peer_user_id,
        msg_id,
        snippet,
        gc_match,
        bool(bind_title),
    )

    text = trimmed
    if text != "/gc" and not bind_title:
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
    if bind_title:
        await _run_bind_flow_for_player(
            event=event,
            cfg=cfg,
            player=player,
            target_title=bind_title,
            listener_label=listener_label,
            ptb_bot=ptb_bot,
        )
        return

    await _run_gc_flow_for_player(
        event,
        cfg,
        player,
        bot_dm_username,
        ptb_bot,
        listener_label=listener_label,
        trigger="outgoing_gc_command",
        delete_trigger_message=True,
    )


def _register_club_event_handlers(
    client: TelegramClient,
    cfg: Any,
    *,
    listener_label: str,
    bot_dm_username: str | None,
    ptb_bot: Any,
) -> None:
    """Attach all dm_gc / group MTProto handlers to a connected client (per cycle)."""

    def _make_dm_gc_incoming_handler(label: str, club_cfg_inner):
        async def _handler(event):
            await handle_dm_gc_incoming(
                event,
                club_cfg_inner,
                bot_dm_username,
                ptb_bot,
                listener_label=label,
            )

        return _handler

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

    def _make_group_add_handler(label: str, club_cfg_inner):
        async def _handler(event):
            await handle_group_add_outgoing(
                event, club_cfg_inner, listener_label=label
            )

        return _handler

    def _make_group_cash_handler(label: str, club_cfg_inner):
        async def _handler(event):
            await handle_group_cash_outgoing(
                event,
                club_cfg_inner,
                listener_label=label,
                ptb_bot=ptb_bot,
            )

        return _handler

    def _make_group_delete_handler(label: str, club_cfg_inner):
        async def _handler(event):
            await handle_group_delete_outgoing(
                event, club_cfg_inner, listener_label=label
            )

        return _handler

    client.add_event_handler(
        _make_dm_gc_incoming_handler(listener_label, cfg),
        events.NewMessage(incoming=True, func=lambda e: e.is_private),
    )
    client.add_event_handler(
        _make_dm_gc_handler(listener_label, cfg),
        events.NewMessage(outgoing=True),
    )
    client.add_event_handler(
        _make_group_add_handler(listener_label, cfg),
        events.NewMessage(outgoing=True),
    )
    client.add_event_handler(
        _make_group_cash_handler(listener_label, cfg),
        events.NewMessage(outgoing=True),
    )
    client.add_event_handler(
        _make_group_delete_handler(listener_label, cfg),
        events.NewMessage(outgoing=True),
    )


async def _start_telethon_clients(
    *,
    bot_dm_username: str | None,
    ptb_bot: Any,
) -> list[TelegramClient]:
    """Connect authorized club sessions and register handlers. Returns started clients."""
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
        setattr(client, "_gg_club_key", cfg.club_key)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                logger.warning(
                    "dm_gc skip club_key=%s: not authorized after connect "
                    "(check session file / Postgres StringSession)",
                    cfg.club_key,
                )
                await client.disconnect()
                continue

            me_who = await client.get_me()
            listener_label = _telethon_user_label(me_who)
            _dm_gc_verbose_info(
                "dm_gc listening club_key=%s listener_account=%s telegram_user_id=%s",
                cfg.club_key,
                listener_label,
                getattr(me_who, "id", "?"),
            )
            _register_club_event_handlers(
                client,
                cfg,
                listener_label=listener_label,
                bot_dm_username=bot_dm_username,
                ptb_bot=ptb_bot,
            )
            started.append(client)
        except Exception as e:
            logger.exception(
                "dm_gc failed to start client club_key=%s: %s",
                cfg.club_key,
                type(e).__name__,
            )
            try:
                if client.is_connected():
                    await client.disconnect()
            except Exception:
                pass

    return started


async def _teardown_listener_cycle(
    started: list[TelegramClient],
    ptb_bot: Any | None,
) -> None:
    for c in started:
        club_key = getattr(c, "_gg_club_key", "?")
        try:
            if c.is_connected():
                await c.disconnect()
        except Exception as e:
            logger.warning(
                "dm_gc disconnect failed club_key=%s: %s",
                club_key,
                type(e).__name__,
            )
    if ptb_bot is not None:
        try:
            await ptb_bot.shutdown()
        except Exception:
            pass
    _clients.clear()
    _loop_holder.pop("ptb_bot", None)


async def _run_listener_cycle(bot_token: str) -> str:
    """One supervised cycle: bootstrap clients, run until disconnect, teardown. Returns exit reason."""
    global _clients

    from telegram import Bot

    ptb_bot = Bot(bot_token)
    await ptb_bot.initialize()
    bot_dm_username: str | None = None
    try:
        me = await ptb_bot.get_me()
        bot_dm_username = me.username.strip() if me.username else None
    except Exception:
        logger.exception("dm_gc Bot.get_me failed")
        await ptb_bot.shutdown()
        return "ptb_bot_get_me_failed"

    started = await _start_telethon_clients(
        bot_dm_username=bot_dm_username,
        ptb_bot=ptb_bot,
    )
    _clients[:] = started
    _loop_holder["ptb_bot"] = ptb_bot

    logger.info(
        "dm_gc listener cycle started telethon_sessions=%s",
        len(started),
    )
    _dm_gc_verbose_info(
        "dm_gc listener bootstrap complete telethon_sessions=%s",
        len(started),
    )

    if not started:
        logger.warning("dm_gc no Telethon clients started this cycle")
        await _teardown_listener_cycle([], ptb_bot)
        return "no_telethon_clients_started"

    exit_reason = "unknown"
    try:
        results = await asyncio.gather(
            *(c.run_until_disconnected() for c in started),
            return_exceptions=True,
        )
        parts: list[str] = []
        for client, result in zip(started, results):
            club_key = getattr(client, "_gg_club_key", "?")
            if isinstance(result, Exception):
                logger.error(
                    "dm_gc Telethon disconnected with error club_key=%s: %s: %s",
                    club_key,
                    type(result).__name__,
                    result,
                )
                parts.append(f"{club_key}={type(result).__name__}")
            else:
                logger.warning(
                    "dm_gc Telethon run_until_disconnected ended club_key=%s result=%r",
                    club_key,
                    result,
                )
                parts.append(f"{club_key}=disconnected")
        exit_reason = "; ".join(parts) if parts else "all_clients_disconnected"
    finally:
        await _teardown_listener_cycle(started, ptb_bot)

    return exit_reason


async def _async_main(bot_token: str) -> None:
    try:
        get_tg_mtproto_credentials()
    except RuntimeError as e:
        logger.error("dm_gc listener: %s", e)
        return

    if not is_dm_gc_listener_enabled():
        return

    initial_delay, max_delay, backoff = get_dm_gc_listener_restart_config()
    delay_sec = initial_delay

    logger.info(
        "dm_gc supervised listener starting (restart_delay=%.0fs max=%.0fs backoff=%.1fx)",
        initial_delay,
        max_delay,
        backoff,
    )

    while not _listener_stop.is_set():
        if not is_dm_gc_listener_enabled():
            logger.info("dm_gc listener disabled via GC_DM_GC_LISTENER_ENABLED")
            break

        _listener_metrics["cycle"] = int(_listener_metrics.get("cycle") or 0) + 1
        _listener_metrics["running"] = True
        _listener_metrics["last_error"] = None

        cycle_num = _listener_metrics["cycle"]
        logger.info("dm_gc listener cycle #%s begin", cycle_num)

        exit_reason = "unknown"
        try:
            exit_reason = await _run_listener_cycle(bot_token)
        except asyncio.CancelledError:
            exit_reason = "cancelled"
            _listener_metrics["last_error"] = exit_reason
            logger.info("dm_gc listener cycle #%s cancelled", cycle_num)
            raise
        except Exception as e:
            exit_reason = f"{type(e).__name__}: {e}"
            _listener_metrics["last_error"] = exit_reason
            logger.exception("dm_gc listener cycle #%s crashed", cycle_num)
        finally:
            _listener_metrics["running"] = False
            _listener_metrics["last_exit_at"] = datetime.now(timezone.utc).isoformat()
            _listener_metrics["last_disconnect_reason"] = exit_reason

        if _listener_stop.is_set() or not is_dm_gc_listener_enabled():
            logger.info("dm_gc supervised listener stopping (reason=%s)", exit_reason)
            break

        _listener_metrics["restart_count"] = int(_listener_metrics.get("restart_count") or 0) + 1
        restart_n = _listener_metrics["restart_count"]
        logger.warning(
            "dm_gc listener cycle #%s ended (%s); supervised restart #%s in %.0fs",
            cycle_num,
            exit_reason,
            restart_n,
            delay_sec,
        )

        slept = 0.0
        while slept < delay_sec and not _listener_stop.is_set():
            chunk = min(1.0, delay_sec - slept)
            await asyncio.sleep(chunk)
            slept += chunk

        delay_sec = min(delay_sec * backoff, max_delay)

    _listener_metrics["running"] = False
    logger.info("dm_gc supervised listener exited")


def start_listener_background(bot_token: str) -> None:
    if not is_dm_gc_listener_enabled():
        _dm_gc_verbose_info("dm_gc listener disabled (GC_DM_GC_LISTENER_ENABLED is false/off)")
        return

    _listener_stop.clear()
    _listener_metrics.update(
        {
            "restart_count": 0,
            "cycle": 0,
            "last_exit_at": None,
            "last_error": None,
            "last_disconnect_reason": None,
            "running": False,
        }
    )

    def runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _loop_holder["loop"] = loop
        try:
            loop.run_until_complete(_async_main(bot_token))
        except Exception:
            logger.exception("dm_gc listener thread exited with error")
        finally:
            _listener_metrics["running"] = False
            loop.close()
            _loop_holder.pop("loop", None)
            logger.info("dm_gc listener thread finished")

    threading.Thread(target=runner, daemon=True, name="mtproto-dm-gc").start()
    logger.info("dm_gc supervised listener thread started")
    _dm_gc_verbose_info("dm_gc listener thread started")


def stop_listener_background() -> None:
    _listener_stop.set()
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
