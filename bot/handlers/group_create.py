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
from telethon.errors.rpcerrorlist import PhoneCodeExpiredError, PhoneNumberInvalidError

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


def normalize_phone_for_mtproto(raw: str) -> str:
    """Normalize for Telethon ``SendCode``: ``+<digits>`` after stripping spaces/separators."""
    stripped = "".join(
        ch for ch in (raw or "").strip() if ch not in " \t\n\r-().[]/"
    )
    digits_all = "".join(ch for ch in stripped if ch.isdigit())
    if not digits_all:
        return ""

    if digits_all.startswith("00") and len(digits_all) >= 10:
        digits_all = digits_all[2:]
    return f"+{digits_all}"


def _e164_digit_count(plus_phone: str) -> int:
    """Count national digits assuming ``+<country><subscriber>``."""

    return len(plus_phone) - 1 if plus_phone.startswith("+") else 0


PHONE_INVALID_REPLY = (
    "Telegram says that phone number is invalid.\n\n"
    "Use international format: a leading + then country code and full number "
    "(no spaces). Examples: +14155552671 (US), +447911123456 (UK).\n\n"
    "If you rely on MT_PROTO_PHONE_* in Heroku Config Vars, update it there "
    "and redeploy—or remove it and enter the phone when the bot asks."
)



def _phone_len_bounds_ok(plus_phone: str) -> bool:
    n = _e164_digit_count(plus_phone)
    return bool(plus_phone) and 8 <= n <= 15


async def request_mtproto_login_code(
    cfg: ClubGcConfig,
    phone_plus: str,
) -> tuple[str | None, str | None]:
    """Returns ``(phone_code_hash, None)`` or ``(None, error_message)``.

    ``error_message`` is ``\"invalid_phone\"`` for Telethon invalid number, or plain
    text for the user (including our ``RuntimeError`` rate-limit hints).
    """

    try:
        return await send_code_for_phone(cfg, phone_plus), None
    except PhoneNumberInvalidError:

        logger.warning(
            "MTProto SendCode: invalid phone format (intl_digits=%s)",
            _e164_digit_count(phone_plus),
        )
        return None, "invalid_phone"
    except RuntimeError as e:


        logger.warning("MTProto SendCode rejected: %s", e)
        return None, str(e)



    except Exception as e:

        logger.exception("send_code_failed %s", type(e).__name__)
        return None, (
            "Could not request a login code (unexpected error; details in server logs). "
            "Try /gc again later."
        )



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
        normalized = normalize_phone_for_mtproto(configured_phone)
        if not _phone_len_bounds_ok(normalized):
            await update.message.reply_text(
                "Configured MT_PROTO_PHONE_* does not look like a valid international "
                "number. Use + then country code and full number (8–15 digits after +). "
                "Fix the Heroku Config Var or remove it and type the phone here."
            )
            _clear_gc_ud(context)
            return ConversationHandler.END

        await update.message.reply_text(f"{prelude}\nRequesting Telegram code for {normalized}...")
        hsh, err = await request_mtproto_login_code(cfg, normalized)
        if err == "invalid_phone":
            await update.message.reply_text(PHONE_INVALID_REPLY)
            _clear_gc_ud(context)
            return ConversationHandler.END
        if err:
            await update.message.reply_text(err)
            _clear_gc_ud(context)
            return ConversationHandler.END

        context.user_data["gc_phone"] = normalized
        context.user_data["gc_phone_code_hash"] = hsh
        await update.message.reply_text("Paste the Telegram login code you received.")
        return GC_CODE_WAIT

    await update.message.reply_text(
        prelude
        + "\n\nReply with your phone in international form: + and country code, then the "
        "full number (digits only after +). Example (US): +14155552671."
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

    phone = normalize_phone_for_mtproto(msg.text or "")

    if not _phone_len_bounds_ok(phone):
        await msg.reply_text(
            "That does not look like a full international phone number "
            "(use +country code then number — usually 8–15 digits after +). Try again."
        )
        return GC_PHONE_WAIT

    hsh, err = await request_mtproto_login_code(cfg, phone)
    if err == "invalid_phone":
        await msg.reply_text(PHONE_INVALID_REPLY)
        return GC_PHONE_WAIT

    if err:
        await msg.reply_text(err)
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

    except PhoneCodeExpiredError:
        logger.info("MTProto sign-in: PhoneCodeExpired club=%s", cfg.club_key)
        await msg.reply_text(
            "Telegram says this login code is expired for the current request.\n\n"
            "Even if you paste immediately, this often means:\n"
            "— Telegram already issued a newer code (e.g. two /gc runs, or two SendCode requests).\n"
            "— More than one bot worker is running; only one should poll Telegram.\n"
            "— You used an older SMS; if you got two texts, only the latest matches the stored hash.\n\n"
            "Fix: run /gc once, wait for a single new code, paste it. Scale Heroku bot worker to 1."
        )
        _clear_gc_ud(context)
        return ConversationHandler.END

    except PhoneCodeInvalidError:
        logger.info("MTProto sign-in: rejected login code")
        await msg.reply_text(
            "That login code doesn't match what Telegram expects. Double-check digits, "
            "or send /gc again for a fresh code."
        )
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
