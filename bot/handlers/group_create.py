"""`/gc`: create support megagroups via MTProto (private chat only; interactive Telethon login)."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError

from club_gc_settings import (
    CLUB_GC_CONFIG,
    ClubGcConfig,
    get_club_config_for_admin,
    get_tg_mtproto_credentials,
)
from bot.services.mtproto_group_create import (
    MtProtoGroupOutcome,
    authenticate_mtproto_code,
    authenticate_mtproto_password,
    create_support_megagroup,
    is_client_authorized,
    send_code_for_phone,
)
from bot.services.support_group_chats import persist_support_group_chat_row

logger = logging.getLogger(__name__)

PRIVATE = filters.ChatType.PRIVATE
TEXT_NON_CMD = filters.TEXT & ~filters.COMMAND
TIMEOUT_SECONDS = 900

GC_PHONE_WAIT, GC_CODE_WAIT, GC_PASSWORD_WAIT = range(3)


def _sanitize_phone(raw: str) -> str:
    s = (raw or "").strip()
    digits = "".join(c for c in s if c.isdigit())
    if "+" in s:
        return f"+{digits}" if digits else ""
    return digits


def _clear_gc_ud(context: ContextTypes.DEFAULT_TYPE) -> None:
    keys = [k for k in context.user_data if str(k).startswith("gc_")]
    for k in keys:
        context.user_data.pop(k, None)


def _club_from_context(context: ContextTypes.DEFAULT_TYPE) -> ClubGcConfig | None:
    key = context.user_data.get("gc_club_key")
    return CLUB_GC_CONFIG.get(key) if isinstance(key, str) else None


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
        initial_message_sent=outcome.initial_message_sent,
        error_message=err_msg,
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
    lines = [
        f"Created group: {outcome.telegram_chat_title}",
        "",
        f"Invite link: {outcome.invite_link or '(export failed — check failed list)'}",
        "",
        "Added:",
    ]
    if outcome.added_users:
        for row in outcome.added_users:
            u = row.get("user") or "?"
            lines.append(f"- {u}")
    else:
        lines.append("- (none)")

    lines.append("")
    lines.append("Failed:")
    human_failed = [
        row
        for row in outcome.failed_users
        if not str(row.get("user") or "").startswith("__")
    ]

    if human_failed:
        for row in human_failed:
            lines.append(f"- {row.get('user')}: {row.get('reason')}")
    else:
        lines.append("- (none)")

    if cfg.group_photo_path and not outcome.group_photo_ok:
        lines.extend(["", "Group photo failed or skipped — see warnings."])

    if outcome.warnings:
        lines.extend(["", "Warnings:"])
        lines.extend([f"- {w}" for w in outcome.warnings[:10]])

    db_line = f"Saved to DB: {'yes' if db_id is not None else 'no'}"
    if db_id is not None:
        db_line += f" (id={db_id})"

    elif db_err:
        db_line += f" ({db_err})"

    lines.extend(["", db_line])


    return "\n".join(lines)


async def _run_creation_send_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    cfg: ClubGcConfig,
    commander_id: int,
) -> int:
    me = await context.bot.get_me()
    bot_username = me.username.strip() if me and me.username else None

    try:

        outcome = await create_support_megagroup(cfg, bot_dm_username=bot_username)
    except Exception as e:


        hint = type(e).__name__

        logger.exception("MTProto megagroup flow failed (%s)", hint)

        if update.effective_message:

            await update.effective_message.reply_text(
                "Group creation failed before completion (details logged). "
                f"Error type: {hint}. Nothing was saved."
            )

        _clear_gc_ud(context)
        return ConversationHandler.END

    pk, persist_err = _persist_row(cfg, outcome, commander_id=commander_id)

    if update.effective_message:

        await update.effective_message.reply_text(
            _compose_status_text(cfg, outcome, pk, persist_err),

        )


    _clear_gc_ud(context)


    return ConversationHandler.END


async def gc_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("Group creation aborted.")
    _clear_gc_ud(context)
    return ConversationHandler.END


async def gc_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="/gc timed out. Run /gc again when ready.",

            )
        except Exception:
            pass
    _clear_gc_ud(context)
    return ConversationHandler.END


async def gc_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if (
        not update.effective_user


        or not update.message


        or not update.effective_chat


        or update.effective_chat.type != ChatType.PRIVATE

    ):
        if update.effective_message:


            await update.effective_message.reply_text("Use /gc in a private chat with this bot.")


        return ConversationHandler.END

    commander = update.effective_user.id
    cfg = get_club_config_for_admin(commander)
    if not cfg:
        await update.message.reply_text("You are not allowed to create groups via /gc.")

        return ConversationHandler.END


    try:
        get_tg_mtproto_credentials()
    except RuntimeError as exc:


        await update.message.reply_text(str(exc))

        return ConversationHandler.END


    _clear_gc_ud(context)
    context.user_data["gc_club_key"] = cfg.club_key

    authorized = await is_client_authorized(cfg)
    if authorized:
        await update.message.reply_text("MTProto session is ready — creating support group...")
        return await _run_creation_send_reply(update, context, cfg, commander)

    prelude = (
        f"Signing in Telegram for {cfg.club_display_name} (MTProto). "
        "Login codes stay in this chat only; they are not logged or saved to the DB."
    )

    configured_phone = cfg.mtproto_phone_number
    if configured_phone:
        normalized = _sanitize_phone(configured_phone)


        await update.message.reply_text(f"{prelude}\nRequesting Telegram code for {normalized}...")
        try:
            hsh = await send_code_for_phone(cfg, normalized)
        except Exception as e:


            hint = type(e).__name__

            logger.exception("send_code_failed %s", hint)

            await update.message.reply_text(
                "Could not request a login code. Check phone/network, TG_API_ID / TG_API_HASH, "
                f"session path permissions, then try again. Telegram error hint: {hint}"
            )


            _clear_gc_ud(context)
            return ConversationHandler.END


        context.user_data["gc_phone"] = normalized
        context.user_data["gc_phone_code_hash"] = hsh

        await update.message.reply_text("Paste the Telegram login code you received.")
        return GC_CODE_WAIT

    await update.message.reply_text(
        prelude + "\n\nReply with phone number incl. country code, e.g. +15551234567"

    )


    return GC_PHONE_WAIT


async def gc_phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    uf = update.effective_user
    if not msg or not uf:
        return ConversationHandler.END

    cfg = _club_from_context(context)
    if not cfg or uf.id != cfg.command_admin_user_id:
        await msg.reply_text("Session lost. Send /gc again.")

        _clear_gc_ud(context)
        return ConversationHandler.END

    phone = _sanitize_phone(msg.text or "")

    if len(phone.lstrip("+")) < 8:


        await msg.reply_text("That phone looks invalid — include country code and try again.")

        return GC_PHONE_WAIT

    try:
        hsh = await send_code_for_phone(cfg, phone)
    except Exception as e:
        hint = type(e).__name__

        logger.exception("send_code_failed %s", hint)

        await msg.reply_text(

            f"Could not request login code ({hint}). Adjust phone or wait and retry /gc."
        )


        _clear_gc_ud(context)
        return ConversationHandler.END


    context.user_data["gc_phone"] = phone
    context.user_data["gc_phone_code_hash"] = hsh

    await msg.reply_text("Paste the Telegram login code you received.")

    return GC_CODE_WAIT


async def gc_code_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    uf = update.effective_user
    if not msg or not uf:
        return ConversationHandler.END


    cfg = _club_from_context(context)
    phone = context.user_data.get("gc_phone")
    hsh = context.user_data.get("gc_phone_code_hash")

    if not cfg or not isinstance(phone, str) or not isinstance(hsh, str) or uf.id != cfg.command_admin_user_id:
        await msg.reply_text("Session lost. Send /gc again.")

        _clear_gc_ud(context)
        return ConversationHandler.END

    code_raw = (msg.text or "").replace(" ", "").strip()

    digits = "".join(c for c in code_raw if c.isdigit())


    code = digits or code_raw

    if len(code) < 3:


        await msg.reply_text("Code looks too short.")
        return GC_CODE_WAIT

    try:
        await authenticate_mtproto_code(
            cfg,
            phone=phone,
            code=code,
            phone_code_hash=hsh,
        )


    except SessionPasswordNeededError:
        await msg.reply_text("This account uses two-factor authentication. Send the Cloud Password here.")

        return GC_PASSWORD_WAIT


    except PhoneCodeInvalidError:
        await msg.reply_text("That code is invalid or expired — request a new login by sending /gc again.")

        _clear_gc_ud(context)


        return ConversationHandler.END


    except Exception as e:

        hint = type(e).__name__

        logger.warning("MTProto SMS sign-in failure %s", hint)

        await msg.reply_text(f"Could not verify the code ({hint}). Fix the input or run /gc again.")

        _clear_gc_ud(context)

        return ConversationHandler.END


    await msg.reply_text("MTProto logged in. Creating group...")



    context.user_data.pop("gc_phone_code_hash", None)
    return await _run_creation_send_reply(update, context, cfg, uf.id)


async def gc_password_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message


    uf = update.effective_user
    if not msg or not uf:
        return ConversationHandler.END



    cfg = _club_from_context(context)
    pwd = msg.text.strip() if msg.text else ""


    if not cfg or not pwd:
        await msg.reply_text("Password empty. Paste your Cloud Password or /cancel.") if msg else None

        return GC_PASSWORD_WAIT



    try:
        await authenticate_mtproto_password(cfg, password=pwd)
    except Exception as e:



        hint = type(e).__name__

        logger.warning("MTProto cloud password rejection %s", hint)

        await msg.reply_text(
            "Cloud Password was not accepted — try again or /cancel."

        )


        return GC_PASSWORD_WAIT


    await msg.reply_text("MTProto authenticated. Creating group...")




    context.user_data.pop("gc_phone_code_hash", None)
    context.user_data.pop("gc_phone", None)

    return await _run_creation_send_reply(update, context, cfg, uf.id)


def get_gc_conversation_handler() -> ConversationHandler:
    combined = PRIVATE & TEXT_NON_CMD
    entry_filter = PRIVATE
    return ConversationHandler(
        entry_points=[CommandHandler("gc", gc_entry, filters=entry_filter)],

        states={
            GC_PHONE_WAIT: [MessageHandler(combined, gc_phone_received)],
            GC_CODE_WAIT: [MessageHandler(combined, gc_code_received)],
            GC_PASSWORD_WAIT: [MessageHandler(combined, gc_password_received)],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL & PRIVATE, gc_timeout),
            ],

        },

        fallbacks=[CommandHandler("cancel", gc_cancel, filters=PRIVATE)],
        conversation_timeout=TIMEOUT_SECONDS,
        name="gc_conv",

        per_chat=True,

        per_user=True,

    )
