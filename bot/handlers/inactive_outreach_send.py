"""Staff /sendinactive — compose outreach DM, preview, confirm/cancel."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import (
    ApplicationHandlerStop,
    ContextTypes,
)

from club_gc_settings import INACTIVE_OUTREACH_CLUB_KEYS, gc_mtproto_operator_telegram_user_ids
from config import ADMIN_USER_IDS
from bot.handlers.flow_cancel import (
    ACTIVE_FLOW_KEY,
    block_if_dm_flow_active,
    clear_active_flow,
    mark_active_flow,
)
from bot.services.inactive_group_outreach_dm import (
    arm_dm_campaign,
    count_dm_eligible_recipients,
    is_dm_batch_running,
    start_dm_batch_job_if_armed,
)

logger = logging.getLogger(__name__)

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


def sendinactive_compose_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    if context.user_data.get(IO_STEP_KEY) == "compose":
        return True
    return (
        "io_club_key" in context.user_data
        and "io_message" not in context.user_data
        and context.user_data.get(IO_STEP_KEY) != "confirm"
    )


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


async def sendinactive_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return

    if chat.type != ChatType.PRIVATE:
        await message.reply_text("Use /sendinactive in a private DM with the bot.")
        raise ApplicationHandlerStop()

    if not _can_use_sendinactive(user.id):
        await message.reply_text("You are not authorized to send inactive outreach DMs.")
        raise ApplicationHandlerStop()

    if is_dm_batch_running():
        await message.reply_text(
            "A DM batch is already running. Wait for it to finish before starting another."
        )
        raise ApplicationHandlerStop()

    club_key, row_id, limit, err = _parse_start_args(message.text)
    if err:
        await message.reply_text(err)
        raise ApplicationHandlerStop()

    count = count_dm_eligible_recipients(club_key=club_key, row_id=row_id, limit=limit)
    if count == 0:
        await message.reply_text(
            f"No eligible staged recipients for {club_key} "
            f"(staged + entity_resolvable + not yet sent)."
        )
        raise ApplicationHandlerStop()

    if await block_if_dm_flow_active(update, context, starting="inactive_outreach_send"):
        raise ApplicationHandlerStop()

    _cleanup_send_flow(context)
    mark_active_flow(context, "inactive_outreach_send")
    context.user_data["io_club_key"] = club_key
    context.user_data["io_row_id"] = row_id
    context.user_data["io_limit"] = limit
    context.user_data["io_admin_id"] = int(user.id)
    context.user_data["io_recipient_count"] = count
    context.user_data[IO_STEP_KEY] = "compose"

    scope = f"club={club_key}"
    if row_id is not None:
        scope += f" row={row_id}"
    if limit is not None:
        scope += f" limit={limit}"

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
    raise ApplicationHandlerStop()


async def sendinactive_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """High-priority private text handler while /sendinactive compose is in progress."""
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    if context.user_data.get(ACTIVE_FLOW_KEY) == "bonus":
        return
    if not sendinactive_compose_active(context):
        return
    if not _can_use_sendinactive(update.effective_user.id):
        return

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

    await sendinactive_confirm(update, context)
    raise ApplicationHandlerStop()


async def sendinactive_compose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return
    if not message.text:
        await message.reply_text("Please send a text message for the outreach copy.")
        return

    body = message.text.strip()
    if not body:
        await message.reply_text("Message cannot be empty. Try again.")
        return

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
    logger.info(
        "sendinactive compose user_id=%s recipients=%s text_len=%s",
        update.effective_user.id if update.effective_user else None,
        count,
        len(body),
    )
    await message.reply_text(preview, reply_markup=_confirm_keyboard())


async def sendinactive_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    if query.data == "io_send_cancel":
        _cleanup_send_flow(context)
        await query.edit_message_text("Cancelled.")
        return

    club_key = str(context.user_data.get("io_club_key") or "round_table")
    message = str(context.user_data.get("io_message") or "").strip()
    admin_id = int(context.user_data.get("io_admin_id") or 0)
    row_id = context.user_data.get("io_row_id")
    limit = context.user_data.get("io_limit")

    if not message or not admin_id:
        _cleanup_send_flow(context)
        await query.edit_message_text("Session expired. Run /sendinactive again.")
        return

    logger.info(
        "sendinactive confirm user_id=%s action=%s recipients=%s",
        admin_id,
        query.data,
        context.user_data.get("io_recipient_count"),
    )

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
        return

    if context.application is not None:
        start_dm_batch_job_if_armed(context.application)

    await query.edit_message_text(
        f"Sending to {armed_count} player(s)…\n"
        "You will get a summary here when the batch finishes."
    )


async def sendinactive_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _cleanup_send_flow(context)
    if update.message:
        await update.message.reply_text("Inactive outreach send cancelled.")
