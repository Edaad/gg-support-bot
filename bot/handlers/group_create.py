"""`/gc`: create support megagroups via MTProto when the Telethon session is already authorized."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import CommandHandler, ContextTypes, filters

from club_gc_settings import ClubGcConfig, get_club_config_for_admin, get_tg_mtproto_credentials
from bot.handlers.groups import send_post_gc_intro_bundle
from bot.services.club import ensure_group_chat_linked
from bot.services.mtproto_group_create import MtProtoGroupOutcome, create_support_megagroup, is_client_authorized
from bot.services.support_group_chats import persist_support_group_chat_row

logger = logging.getLogger(__name__)


def _persist_row(
    cfg: ClubGcConfig,
    outcome: MtProtoGroupOutcome,
    *,
    commander_id: int,
) -> tuple[int | None, str | None]:
    errs = list(outcome.warnings)
    if outcome.error_hint:
        errs.append(outcome.error_hint)
    err_msg = "; ".join(errs) if errs else None

    cid = outcome.telegram_chat_id
    if cid is None:
        return None, err_msg or "missing chat id"

    pk, persist_err = persist_support_group_chat_row(
        club_key=cfg.club_key,
        club_display_name=cfg.club_display_name,
        telegram_chat_id=cid,
        telegram_chat_title=outcome.telegram_chat_title,
        invite_link=outcome.invite_link,
        created_by_telegram_user_id=commander_id,
        mtproto_session_name=cfg.mtproto_session,
        added_users=outcome.added_users,
        failed_users=outcome.failed_users,
        group_photo_path=cfg.group_photo_path,
        initial_group_message_sent=outcome.initial_message_sent,
        last_error_message=err_msg,
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
) -> str:
    """Short operator-facing reply; details stay in logs / DB."""

    title = outcome.telegram_chat_title.strip() or "(untitled)"
    link = outcome.invite_link.strip() if outcome.invite_link else ""

    chunks: list[str] = [title, link or "(invite link unavailable)"]

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

    cid = outcome.telegram_chat_id
    if cid is not None and db_id is None:
        chunks.append("")
        chunks.append(f"Audit DB: {db_err or 'save failed — see logs'}")

    return "\n".join(chunks)


_EXPIRED_REPLY = (
    "MTProto session for your club has expired or isn’t logged in.\n\n"
    "Sign in once from GG Dashboard → Telegram login (navigation tab). Then send /gc again here."
)


async def gc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a megagroup when authorized; otherwise point operators to Dashboard login."""

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

    authorized = await is_client_authorized(cfg)
    if authorized:
        me = await context.bot.get_me()
        bot_username = me.username.strip() if me and me.username else None

        try:

            outcome = await create_support_megagroup(cfg, bot_dm_username=bot_username)
        except Exception as e:

            hint = type(e).__name__

            logger.exception("MTProto megagroup flow failed (%s)", hint)

            await update.message.reply_text(
                "Group creation failed before completion (details logged). "
                f"Error type: {hint}. Nothing was saved."
            )


            return

        pk, persist_err = _persist_row(cfg, outcome, commander_id=commander)

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


                    logger.exception("post-/gc dashboard intro bundle failed chat_id=%s: %s", cid, type(e).__name__)

        await update.message.reply_text(
            _compose_status_text(cfg, outcome, pk, persist_err),
        )
        return

    await update.message.reply_text(_EXPIRED_REPLY)


def get_gc_handler() -> CommandHandler:
    return CommandHandler("gc", gc_command, filters=filters.ChatType.PRIVATE)
