"""`/gc`: create support groups via MTProto when the Telethon session is already authorized."""

from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import CommandHandler, ContextTypes, filters

from club_gc_settings import ClubGcConfig, get_club_config_for_admin, get_tg_mtproto_credentials
from bot.handlers.groups import send_post_gc_intro_bundle
from bot.services.club import ensure_group_chat_linked
from bot.services.mtproto_group_create import (
    MtProtoGroupOutcome,
    create_support_group,
    is_client_authorized,
    resolve_telegram_user_marker,
    send_player_dm_via_club,
)
from bot.services.player_support_dm_messages import (
    PLAYER_ADDED_SUCCESS_MESSAGE,
    PLAYER_INVITE_FALLBACK_MESSAGE,
)
from bot.services.support_group_chats import (
    fetch_support_group_chat_by_club_player,
    persist_support_group_chat_row,
)

logger = logging.getLogger(__name__)

_GC_USAGE = (
    "Usage:\n"
    "  /gc @username — create a player support group and bind the player\n"
    "  /gc <telegram_user_id> — same, by numeric id\n"
    "  /gc — create a generic group (no player binding)"
)


def parse_gc_player_args(args: list[str]) -> str | None:
    """Return Telethon lookup marker from `/gc` args, or None for generic `/gc`."""

    if not args:
        return None
    joined = " ".join(args).strip()
    if not joined:
        return None
    if joined.lstrip("-").isdigit():
        return joined
    if joined.startswith("@"):
        return joined
    return f"@{joined.lstrip('@')}"


def _player_fields(player_user: Any) -> tuple[int, str | None, str | None]:
    uid = int(player_user.id)
    uname = player_user.username.strip() if getattr(player_user, "username", None) else None
    dname = (
        f"{getattr(player_user, 'first_name', '') or ''} {getattr(player_user, 'last_name', '') or ''}"
    ).strip() or None
    return uid, uname, dname


def _persist_row(
    cfg: ClubGcConfig,
    outcome: MtProtoGroupOutcome,
    *,
    commander_id: int,
    player_user: Any | None = None,
    player_dm_status: str | None = None,
    extra_errors: list[str] | None = None,
) -> tuple[int | None, str | None]:
    errs = list(outcome.warnings)
    if outcome.error_hint:
        errs.append(outcome.error_hint)
    if extra_errors:
        errs.extend(extra_errors)
    for row in outcome.link_join_failures:
        errs.append(
            f"link_join:{row.get('user') or '?'}:{row.get('reason') or 'unknown'}"
        )
    err_msg = "; ".join(errs) if errs else None

    added_users = list(outcome.added_users)
    added_users.extend(outcome.link_joined_users)
    added_users.extend(outcome.promoted_admins)
    failed_users = list(outcome.failed_users)
    failed_users.extend(outcome.link_join_failures)

    cid = outcome.telegram_chat_id
    if cid is None:
        return None, err_msg or "missing chat id"

    player_id: int | None = None
    player_username: str | None = None
    player_display_name: str | None = None
    if player_user is not None:
        player_id, player_username, player_display_name = _player_fields(player_user)

    pk, persist_err = persist_support_group_chat_row(
        club_key=cfg.club_key,
        club_display_name=cfg.club_display_name,
        telegram_chat_id=cid,
        telegram_chat_title=outcome.telegram_chat_title,
        invite_link=outcome.invite_link,
        created_by_telegram_user_id=commander_id,
        mtproto_session_name=cfg.mtproto_session,
        added_users=added_users,
        failed_users=failed_users,
        group_photo_path=cfg.group_photo_path,
        initial_group_message_sent=outcome.initial_message_sent,
        last_error_message=err_msg,
        player_telegram_user_id=player_id,
        player_username=player_username,
        player_display_name=player_display_name,
        player_dm_status=player_dm_status,
    )
    if persist_err:
        logger.warning(
            "support_group_chats insert failed for chat_id=%s: %s",
            cid,
            persist_err,
        )

    return pk, persist_err


def _compose_status_text(
    cfg: ClubGcConfig,
    outcome: MtProtoGroupOutcome,
    db_id: int | None,
    db_err: str | None,
    *,
    player_marker: str | None = None,
    player_dm_note: str | None = None,
) -> str:
    """Short operator-facing reply; details stay in logs / DB."""

    title = outcome.telegram_chat_title.strip() or "(untitled)"
    link = outcome.invite_link.strip() if outcome.invite_link else ""

    chunks: list[str] = [title, link or "(invite link unavailable)"]
    if player_marker:
        chunks.insert(0, f"Player: {player_marker}")

    human_failed = [
        row
        for row in outcome.failed_users
        if not str(row.get("user") or "").startswith("__")
    ]
    if human_failed:
        chunks.append("")
        chunks.append(
            "\n".join(
                f"Failed: {row.get('user') or '?'} — {row.get('reason') or 'unknown'}"
                for row in human_failed[:12]
            )
        )

    if cfg.group_photo_path and not outcome.group_photo_ok:
        chunks.append("Photo skipped or failed.")

    if outcome.warnings:
        chunks.append("")
        chunks.extend(outcome.warnings[:8])

    if player_dm_note:
        chunks.append("")
        chunks.append(player_dm_note)

    cid = outcome.telegram_chat_id
    if cid is not None and db_id is None:
        chunks.append("")
        chunks.append(f"Audit DB: {db_err or 'save failed — see logs'}")

    return "\n".join(chunks)


_EXPIRED_REPLY = (
    "MTProto session for your club has expired or isn’t logged in.\n\n"
    "Sign in once from GG Dashboard → Telegram login (navigation tab). Then send /gc again here."
)


async def _finish_gc_creation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    cfg: ClubGcConfig,
    commander_id: int,
    outcome: MtProtoGroupOutcome,
    player_user: Any | None = None,
    player_marker: str | None = None,
) -> None:
    assert update.message is not None

    player_dm_status: str | None = None
    player_dm_note: str | None = None
    extra_errors: list[str] = []

    if player_user is not None and outcome.telegram_chat_id is not None:
        if outcome.player_direct_add_ok:
            dm_body = PLAYER_ADDED_SUCCESS_MESSAGE
            player_dm_status = "player_added_success"
        else:
            link = (outcome.invite_link or "").strip() or "(invite link unavailable)"
            dm_body = PLAYER_INVITE_FALLBACK_MESSAGE.format(invite_link=link)
            player_dm_status = "player_invite_fallback"

        dm_ok, dm_err = await send_player_dm_via_club(cfg, player_user, dm_body)
        if dm_ok:
            player_dm_note = "Player DM: sent."
        else:
            player_dm_status = (player_dm_status or "player_dm") + "_dm_failed"
            player_dm_note = f"Player DM: failed ({dm_err or 'unknown'})."
            if dm_err:
                extra_errors.append(f"player_dm:{dm_err}")

    pk, persist_err = _persist_row(
        cfg,
        outcome,
        commander_id=commander_id,
        player_user=player_user,
        player_dm_status=player_dm_status,
        extra_errors=extra_errors or None,
    )

    if persist_err == "duplicate_club_player" and player_user is not None:
        existing = fetch_support_group_chat_by_club_player(cfg.club_key, int(player_user.id))
        if existing:
            await update.message.reply_text(
                "This player already has a support group.\n\n"
                f"{existing.telegram_chat_title}\n"
                f"{existing.invite_link or '(no invite link stored)'}"
            )
            return

    cid = outcome.telegram_chat_id
    dash_club_id = cfg.link_club_id
    if cid is not None:
        linked = ensure_group_chat_linked(cid, dash_club_id, outcome.telegram_chat_title)
        if not linked:
            logger.warning(
                "/gc ensure_group_chat_linked failed chat_id=%s dashboard_club_id=%s (inactive club or bad id)",
                cid,
                dash_club_id,
            )
        elif context.bot:
            try:
                await send_post_gc_intro_bundle(
                    context.bot,
                    cid,
                    dash_club_id,
                    outcome.telegram_chat_title,
                )
            except Exception as e:
                logger.exception(
                    "post-/gc dashboard intro bundle failed chat_id=%s: %s",
                    cid,
                    type(e).__name__,
                )

    await update.message.reply_text(
        _compose_status_text(
            cfg,
            outcome,
            pk,
            persist_err,
            player_marker=player_marker,
            player_dm_note=player_dm_note,
        ),
    )


async def gc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a basic support group when authorized; otherwise point operators to Dashboard login."""

    if (
        not update.effective_user
        or not update.message
        or not update.effective_chat
        or update.effective_chat.type != ChatType.PRIVATE
    ):
        if update.effective_message:
            await update.effective_message.reply_text("Use /gc in a private chat with this bot.")
        return

    commander = update.effective_user.id
    cfg = get_club_config_for_admin(commander)
    if not cfg:
        await update.message.reply_text("You are not allowed to create groups via /gc.")
        return

    try:
        get_tg_mtproto_credentials()
    except RuntimeError as exc:
        await update.message.reply_text(str(exc))
        return

    player_marker = parse_gc_player_args(context.args or [])
    player_user = None
    if player_marker:
        player_user, resolve_err = await resolve_telegram_user_marker(cfg, player_marker)
        if player_user is None:
            await update.message.reply_text(
                f"Could not resolve player {player_marker} ({resolve_err or 'unknown'}).\n\n{_GC_USAGE}"
            )
            return
        existing = fetch_support_group_chat_by_club_player(cfg.club_key, int(player_user.id))
        if existing:
            await update.message.reply_text(
                "This player already has a support group.\n\n"
                f"{existing.telegram_chat_title}\n"
                f"{existing.invite_link or '(no invite link stored)'}"
            )
            return

    authorized = await is_client_authorized(cfg)
    if not authorized:
        await update.message.reply_text(_EXPIRED_REPLY)
        return

    me = await context.bot.get_me()
    bot_username = me.username.strip() if me and me.username else None

    link_join_client = None
    try:
        from bot.services.mtproto_dm_gc_listener import get_listener_client

        link_join_client = get_listener_client(cfg.club_key)
    except Exception:
        link_join_client = None

    try:
        outcome = await create_support_group(
            cfg,
            bot_dm_username=bot_username,
            player_user=player_user,
            link_join_client=link_join_client,
        )
    except Exception as e:
        hint = type(e).__name__
        logger.exception("MTProto group creation failed (%s)", hint)
        if hint == "ChannelsTooMuchError":
            from bot.services.mtproto_track_contact import notify_club_gc_channels_too_much

            player_label = player_marker or "(generic group)"
            await notify_club_gc_channels_too_much(
                cfg,
                player_label=player_label,
                trigger="bot /gc",
            )
            await update.message.reply_text(
                "Group creation failed: the club MTProto account has joined too many "
                "Telegram groups/channels. Leave inactive groups on that account and retry. "
                "The club admin was DM'd on this bot."
            )
        else:
            await update.message.reply_text(
                "Group creation failed before completion (details logged). "
                f"Error type: {hint}. Nothing was saved."
            )
        return

    await _finish_gc_creation(
        update,
        context,
        cfg=cfg,
        commander_id=commander,
        outcome=outcome,
        player_user=player_user,
        player_marker=player_marker,
    )


def get_gc_handler() -> CommandHandler:
    return CommandHandler("gc", gc_command, filters=filters.ChatType.PRIVATE)
