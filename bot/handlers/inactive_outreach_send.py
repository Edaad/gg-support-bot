"""Staff /sendinactive — compose outreach DM, preview, confirm/cancel."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from club_gc_settings import INACTIVE_OUTREACH_CLUB_KEYS, gc_mtproto_operator_telegram_user_ids
from config import ADMIN_USER_IDS
from bot.handlers.flow_cancel import clear_active_flow, mark_active_flow
from bot.services.inactive_group_outreach_dm import (
    arm_dm_campaign,
    count_dm_eligible_recipients,
    is_dm_batch_running,
    start_dm_batch_job_if_armed,
)

logger = logging.getLogger(__name__)

(IO_COMPOSE, IO_CONFIRM) = range(2)

IO_STEP_KEY = "io_step"

_IO_USER_KEYS = (
    "io_club_key",
    "io_row_id",
    "io_limit",
    "io_message",
    "io_admin_id",
    "io_recipient_count",
    IO_STEP_KEY,
)


def sendinactive_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return context.user_data.get(IO_STEP_KEY) in ("compose", "confirm")


def _can_use_sendinactive(user_id: int) -> bool:
    if user_id in ADMIN_USER_IDS:
        return True
    return user_id in gc_mtproto_operator_telegram_user_ids()


def _cleanup_send_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_active_flow(context)
    for key in _IO_USER_KEYS:
        context.user_data.pop(key, None)


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data="io_send_confirm"),
                InlineKeyboardButton("Cancel", callback_data="io_send_cancel"),
            ]
        ]
    )


def _parse_start_args(text: str | None) -> tuple[str, int | None, int | None, str | None]:
    """Return (club_key, row_id, limit, error)."""

    parts = (text or "").strip().split(maxsplit=1)
    args_str = parts[1].strip() if len(parts) > 1 else ""
    tokens = args_str.split() if args_str else []

    club_key = "round_table"
    row_id: int | None = None
    limit: int | None = None
    idx = 0

    if tokens and tokens[0] in INACTIVE_OUTREACH_CLUB_KEYS:
        club_key = tokens[0]
        idx = 1

    while idx < len(tokens):
        tok = tokens[idx].lower()
        if tok == "row":
            if idx + 1 >= len(tokens) or not tokens[idx + 1].isdigit():
                return club_key, None, None, "Usage: /sendinactive [club_key] row <outreach_row_id>"
            row_id = int(tokens[idx + 1])
            idx += 2
            continue
        if tok == "limit":
            if idx + 1 >= len(tokens) or not tokens[idx + 1].isdigit():
                return club_key, None, None, "Usage: /sendinactive [club_key] limit <n>"
            limit = int(tokens[idx + 1])
            idx += 2
            continue
        return club_key, None, None, f"Unknown argument: {tokens[idx]!r}"

    if club_key not in INACTIVE_OUTREACH_CLUB_KEYS:
        return club_key, None, None, f"Unknown club_key: {club_key!r}."

    return club_key, row_id, limit, None


async def sendinactive_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return ConversationHandler.END

    if chat.type != ChatType.PRIVATE:
        await message.reply_text("Use /sendinactive in a private DM with the bot.")
        return ConversationHandler.END

    if not _can_use_sendinactive(user.id):
        await message.reply_text("You are not authorized to send inactive outreach DMs.")
        return ConversationHandler.END

    if is_dm_batch_running():
        await message.reply_text(
            "A DM batch is already running. Wait for it to finish before starting another."
        )
        return ConversationHandler.END

    club_key, row_id, limit, err = _parse_start_args(message.text)
    if err:
        await message.reply_text(err)
        return ConversationHandler.END

    count = count_dm_eligible_recipients(club_key=club_key, row_id=row_id, limit=limit)
    if count == 0:
        await message.reply_text(
            f"No eligible staged recipients for {club_key} "
            f"(staged + entity_resolvable + not yet sent)."
        )
        return ConversationHandler.END

    _cleanup_send_flow(context)
    mark_active_flow(context, "inactive_outreach_send")
    context.user_data["io_club_key"] = club_key
    context.user_data["io_row_id"] = row_id
    context.user_data["io_limit"] = limit
    context.user_data["io_admin_id"] = int(user.id)
    context.user_data["io_recipient_count"] = count

    scope = f"club={club_key}"
    if row_id is not None:
        scope += f" row={row_id}"
    if limit is not None:
        scope += f" limit={limit}"

    context.user_data[IO_STEP_KEY] = "compose"
    logger.info(
        "sendinactive start user_id=%s club=%s row_id=%s limit=%s recipients=%s",
        user.id,
        club_key,
        row_id,
        limit,
        count,
    )

    await message.reply_text(
        f"Inactive outreach DM ({scope})\n"
        f"Recipients: {count}\n\n"
        "Send the message you want inactive players to receive."
    )
    return IO_COMPOSE


async def sendinactive_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """High-priority private text handler while /sendinactive compose is in progress."""
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    if context.user_data.get(IO_STEP_KEY) != "compose":
        return
    if not _can_use_sendinactive(update.effective_user.id):
        return

    logger.info(
        "sendinactive compose user_id=%s text_len=%s",
        update.effective_user.id,
        len(update.message.text or ""),
    )
    await sendinactive_compose(update, context)
    raise ApplicationHandlerStop()


async def sendinactive_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """High-priority callback handler for outreach preview Confirm/Cancel."""
    query = update.callback_query
    if not query or not query.data:
        return
    if context.user_data.get(IO_STEP_KEY) != "confirm":
        return
    if not query.data.startswith("io_send_"):
        return
    if not update.effective_user or not _can_use_sendinactive(update.effective_user.id):
        return

    logger.info(
        "sendinactive confirm user_id=%s action=%s",
        update.effective_user.id,
        query.data,
    )
    await sendinactive_confirm(update, context)
    raise ApplicationHandlerStop()


async def sendinactive_compose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    if not message:
        return IO_COMPOSE
    if not message.text:
        await message.reply_text("Please send a text message for the outreach copy.")
        return IO_COMPOSE

    body = message.text.strip()
    if not body:
        await message.reply_text("Message cannot be empty. Try again.")
        return IO_COMPOSE

    context.user_data["io_message"] = body
    count = int(context.user_data.get("io_recipient_count") or 0)

    preview = (
        f"Preview — sending to {count} player(s):\n\n"
        f"{body}\n\n"
        "Confirm to start sending, or Cancel."
    )
    if len(preview) > 4096:
        preview = preview[:4090] + "…"

    context.user_data[IO_STEP_KEY] = "confirm"
    await message.reply_text(preview, reply_markup=_confirm_keyboard())
    return IO_CONFIRM


async def sendinactive_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query:
        return IO_CONFIRM
    await query.answer()

    if query.data == "io_send_cancel":
        _cleanup_send_flow(context)
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    club_key = str(context.user_data.get("io_club_key") or "round_table")
    message = str(context.user_data.get("io_message") or "").strip()
    admin_id = int(context.user_data.get("io_admin_id") or 0)
    row_id = context.user_data.get("io_row_id")
    limit = context.user_data.get("io_limit")

    if not message or not admin_id:
        _cleanup_send_flow(context)
        await query.edit_message_text("Session expired. Run /sendinactive again.")
        return ConversationHandler.END

    ok, err, armed_count = arm_dm_campaign(
        club_key=club_key,
        message=message,
        started_by_user_id=admin_id,
        row_id=int(row_id) if row_id is not None else None,
        limit=int(limit) if limit is not None else None,
    )
    _cleanup_send_flow(context)

    if not ok:
        await query.edit_message_text(err or "Failed to arm campaign.")
        return ConversationHandler.END

    if context.application is not None:
        start_dm_batch_job_if_armed(context.application)

    await query.edit_message_text(
        f"Sending to {armed_count} player(s)…\n"
        "You will get a summary here when the batch finishes."
    )
    return ConversationHandler.END


async def sendinactive_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _cleanup_send_flow(context)
    if update.message:
        await update.message.reply_text("Inactive outreach send cancelled.")
    return ConversationHandler.END


def get_sendinactive_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler(
                "sendinactive",
                sendinactive_start,
                filters=filters.ChatType.PRIVATE,
            )
        ],
        states={
            IO_COMPOSE: [
                MessageHandler(
                    filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
                    sendinactive_compose,
                ),
                CommandHandler("cancel", sendinactive_cancel),
            ],
            IO_CONFIRM: [
                CallbackQueryHandler(sendinactive_confirm, pattern=r"^io_send_(confirm|cancel)$"),
                CommandHandler("cancel", sendinactive_cancel),
            ],
        },
        fallbacks=[CommandHandler("cancel", sendinactive_cancel)],
        per_chat=False,
        per_user=True,
        allow_reentry=True,
    )
