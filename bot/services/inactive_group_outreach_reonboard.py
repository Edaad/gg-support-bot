"""Re-onboard inactive outreach players after they reply to outreach DM."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from telethon.tl.types import User

from bot.handlers.groups import send_post_gc_intro_bundle
from bot.services.club import ensure_group_chat_linked
from bot.services.mtproto_group_create import create_support_group
from bot.services.mtproto_group_delete import erase_group_chat
from bot.services.player_details import remove_chat_id_from_club_bindings
from bot.services.player_support_dm_messages import (
    PLAYER_ADDED_SUCCESS_MESSAGE,
    PLAYER_EXISTING_GROUP_MESSAGE,
    PLAYER_INVITE_FALLBACK_MESSAGE,
)
from bot.services.support_group_chats import (
    persist_support_group_chat_row,
    supersede_support_group_chat_binding,
)
from db.connection import get_db
from db.models import InactiveGroupOutreachRow

logger = logging.getLogger(__name__)


async def _send_player_dm_safe(client, player: User, body: str) -> tuple[bool, str | None]:
    try:
        await client.send_message(player, body)
        return True, None
    except Exception as exc:
        return False, type(exc).__name__


def _mark_outreach_reonboarded(
    row_id: int,
    *,
    new_chat_id: int | None = None,
    error: str | None = None,
    reply_at: datetime | None = None,
    erased_at: datetime | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = session.get(InactiveGroupOutreachRow, int(row_id))
        if row is None:
            return
        if reply_at is not None:
            row.reply_received_at = reply_at
        if erased_at is not None:
            row.old_group_erased_at = erased_at
        if error:
            row.dm_status = "reonboard_failed"
            row.reonboard_error = error[:2000]
        else:
            row.dm_status = "reonboarded"
            row.reonboard_error = None
            if new_chat_id is not None:
                row.reonboard_new_chat_id = int(new_chat_id)
        row.updated_at = now
        session.commit()


async def run_inactive_outreach_reonboard(
    *,
    client,
    cfg,
    player: User,
    outreach_row: InactiveGroupOutreachRow,
    bot_dm_username: str | None,
    ptb_bot,
    listener_label: str,
) -> bool:
    """Erase old megagroup and create fresh basic group with preserved title. Returns True on success."""

    if outreach_row.dm_status == "reonboarded":
        await _send_player_dm_safe(client, player, PLAYER_EXISTING_GROUP_MESSAGE)
        return True

    if outreach_row.dm_status != "sent":
        return False

    now = datetime.now(timezone.utc)
    old_chat_id = int(outreach_row.telegram_chat_id)
    title_override = str(outreach_row.group_title)
    player_id = int(player.id)
    uname = player.username.strip() if player.username else None
    dname = (f"{player.first_name or ''} {player.last_name or ''}").strip() or None

    supersede_support_group_chat_binding(
        club_key=cfg.club_key,
        telegram_chat_id=old_chat_id,
        reason=f"inactive outreach re-onboard; retiring chat {old_chat_id}",
    )

    erase_err = await erase_group_chat(client, cfg=cfg, chat_id=old_chat_id)
    if erase_err:
        logger.warning(
            "inactive_outreach_reonboard: erase failed club=%s chat=%s err=%s",
            cfg.club_key,
            old_chat_id,
            erase_err,
        )
        _mark_outreach_reonboarded(
            outreach_row.id,
            error=f"erase_failed:{erase_err}",
            reply_at=now,
        )
        from bot.services.slack_ops_notify import notify_slack_ops

        await notify_slack_ops(
            f"Inactive outreach re-onboard erase failed club={cfg.club_key} "
            f"chat={old_chat_id} player={player_id}: {erase_err}",
            source="inactive_outreach_reonboard",
        )
        return False

    remove_chat_id_from_club_bindings(club_id=int(cfg.link_club_id), chat_id=old_chat_id)

    try:
        outcome = await create_support_group(
            cfg,
            bot_dm_username=bot_dm_username,
            player_user=player,
            link_join_client=client,
            title_override=title_override,
        )
    except Exception as exc:
        err_name = type(exc).__name__
        logger.exception(
            "inactive_outreach_reonboard: create_support_group failed club=%s player=%s",
            cfg.club_key,
            player_id,
        )
        _mark_outreach_reonboarded(
            outreach_row.id,
            error=f"create_failed:{err_name}",
            reply_at=now,
            erased_at=now,
        )
        return False

    cid = outcome.telegram_chat_id
    if cid is None:
        _mark_outreach_reonboarded(
            outreach_row.id,
            error="create_missing_chat_id",
            reply_at=now,
            erased_at=now,
        )
        return False

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

    errs = list(outcome.warnings or [])
    if outcome.error_hint:
        errs.append(outcome.error_hint)
    if not dm_ok and dm_err:
        errs.append(f"player_dm:{dm_err}")
    last_err = "; ".join(errs) if errs else None

    me = await client.get_me()
    admin_id = int(me.id)

    pk, perr = persist_support_group_chat_row(
        club_key=cfg.club_key,
        club_display_name=cfg.club_display_name,
        telegram_chat_id=int(cid),
        telegram_chat_title=outcome.telegram_chat_title or title_override,
        invite_link=outcome.invite_link,
        created_by_telegram_user_id=admin_id,
        mtproto_session_name=cfg.mtproto_session,
        added_users=outcome.added_users,
        failed_users=outcome.failed_users,
        group_photo_path=cfg.group_photo_path,
        initial_group_message_sent=outcome.initial_message_sent,
        last_error_message=last_err,
        player_telegram_user_id=player_id,
        player_username=uname,
        player_display_name=dname,
        player_dm_status=dm_status + ("_dm_failed" if not dm_ok else ""),
    )

    if pk is None:
        _mark_outreach_reonboarded(
            outreach_row.id,
            error=f"persist_failed:{perr or 'unknown'}",
            reply_at=now,
            erased_at=now,
        )
        return False

    linked = ensure_group_chat_linked(int(cid), cfg.link_club_id, outcome.telegram_chat_title)
    if linked and ptb_bot is not None:
        try:
            await send_post_gc_intro_bundle(
                ptb_bot, int(cid), cfg.link_club_id, outcome.telegram_chat_title or title_override
            )
        except Exception:
            logger.exception(
                "inactive_outreach_reonboard: post_intro_bundle failed chat_id=%s",
                cid,
            )

    _mark_outreach_reonboarded(
        outreach_row.id,
        new_chat_id=int(cid),
        reply_at=now,
        erased_at=now,
    )
    logger.info(
        "inactive_outreach_reonboard: ok club=%s listener=%s player=%s old=%s new=%s",
        cfg.club_key,
        listener_label,
        player_id,
        old_chat_id,
        cid,
    )
    return True
