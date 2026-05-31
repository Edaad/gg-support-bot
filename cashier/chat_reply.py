"""Send user-visible messages and errors in the chat where GGCashier was invoked."""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import BadRequest, TelegramError

logger = logging.getLogger(__name__)

TIMEOUT_USER_MESSAGE = (
    "Cashout timed out due to inactivity (15 minutes). "
    "The job was cancelled. Send /cashout or run /cash in the group to start again."
)

GENERIC_ERROR_MESSAGE = (
    "Something went wrong. Try again, or send /cancel to reset."
)


def user_facing_error(exc: BaseException | None) -> str:
    """Map an exception to a safe message for Telegram chat."""
    if exc is None:
        return GENERIC_ERROR_MESSAGE
    text = str(exc).strip()
    lower = text.lower()
    if "cashier_cashout_jobs" in lower and (
        "does not exist" in lower or "undefinedtable" in lower
    ):
        return (
            "Database setup incomplete (cashier_cashout_jobs table missing). "
            "Run: python migrate_cashier_jobs.py"
        )
    if "foreignkeyviolation" in lower or "foreign key constraint" in lower:
        return (
            "Payment method could not be saved on this cashout job (database FK mismatch). "
            "Run: python migrate_cashier_jobs_drop_method_fks.py"
        )
        return "Database connection error. Try again in a moment."
    if "query is too old" in lower or "query id is invalid" in lower:
        return (
            "That button expired (cashier was offline or you waited too long). "
            "Run /cash in the group again, or send /cashout here."
        )
    if isinstance(exc, TelegramError) and text:
        return f"Telegram error: {text[:200]}"
    return GENERIC_ERROR_MESSAGE


async def safe_answer_callback(
    query,
    *,
    text: str | None = None,
    show_alert: bool = False,
) -> bool:
    """Answer callback query; return False if Telegram rejected it (expired tap)."""
    try:
        if text:
            await query.answer(text[:200], show_alert=show_alert)
        else:
            await query.answer()
        return True
    except BadRequest as exc:
        msg = str(exc).lower()
        if "query is too old" in msg or "query id is invalid" in msg:
            logger.warning("callback query expired, continuing without answer: %s", exc)
            return False
        raise


async def reply_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    chat_id: Optional[int] = None,
) -> None:
    """Reply or send a plain text message in the invocation chat."""
    target_chat = chat_id
    if target_chat is None and update.effective_chat:
        target_chat = update.effective_chat.id

    if update.callback_query:
        query = update.callback_query
        await safe_answer_callback(query)
        try:
            await query.edit_message_text(text)
            return
        except BadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                logger.debug("reply_text edit BadRequest: %s", exc)
        except Exception:
            logger.debug("reply_text edit failed, sending new message", exc_info=True)

    if update.message:
        try:
            await update.message.reply_text(text)
            return
        except Exception:
            logger.debug("reply_text reply failed", exc_info=True)

    if target_chat is not None:
        await context.bot.send_message(chat_id=target_chat, text=text)


async def reply_error(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    chat_id: Optional[int] = None,
    alert: bool = False,
) -> None:
    """Show an error in chat (callback alert + message when possible)."""
    if update.callback_query:
        await safe_answer_callback(
            update.callback_query, text=text[:200] if alert else None, show_alert=alert
        )
    await reply_text(update, context, text, chat_id=chat_id)


async def reply_exception(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    exc: BaseException,
    *,
    chat_id: Optional[int] = None,
    prefix: str = "Error",
) -> None:
    """Log and show a user-safe error in chat."""
    logger.exception("%s: %s", prefix, exc)
    detail = user_facing_error(exc)
    await reply_error(
        update,
        context,
        f"{prefix}: {detail}",
        chat_id=chat_id,
        alert=bool(update.callback_query),
    )


def remember_wizard_chat(context: ContextTypes.DEFAULT_TYPE, update: Update) -> None:
    """Store chat id so timeout handler can message the user."""
    if update.effective_chat:
        context.user_data["gc_wizard_chat_id"] = update.effective_chat.id


def wizard_chat_id(context: ContextTypes.DEFAULT_TYPE, update: Update) -> Optional[int]:
    cid = context.user_data.get("gc_wizard_chat_id")
    if cid is not None:
        return int(cid)
    if update.effective_chat:
        return update.effective_chat.id
    return None
