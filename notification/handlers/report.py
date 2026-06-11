"""Reply-to /report handler for buggy payment notifications."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from notification.chat_id import telegram_chat_ids_match
from notification.constants import (
    PAYMENT_NOTIFICATION_CHAT_ID_ENV,
    notification_report_to_user_id,
)
from notification.handlers._chat import notification_chat_id

logger = logging.getLogger(__name__)

REPORT_REASON = 0

_REPORT_MSG_ID_KEY = "report_notification_message_id"
_REPORT_MSG_TEXT_KEY = "report_notification_text"
_REPORT_CHAT_ID_KEY = "report_notification_chat_id"


def _message_body(message) -> str:
    text = (getattr(message, "text", None) or "").strip()
    if text:
        return text
    return (getattr(message, "caption", None) or "").strip()


def _is_notification_chat(update: Update) -> bool:
    if not update.effective_chat:
        return False
    expected = notification_chat_id()
    if expected is None:
        return False
    return telegram_chat_ids_match(int(update.effective_chat.id), expected)


def format_report_ticket(
    *,
    reporter_username: str | None,
    reporter_user_id: int,
    notification_chat_id: int,
    notification_message_id: int,
    notification_text: str,
    reason: str,
) -> str:
    reporter_label = f"@{reporter_username}" if reporter_username else "(no username)"
    body = notification_text or "(no text)"
    return "\n".join(
        [
            "Notification bug report",
            "",
            f"Reporter: {reporter_label} (id={reporter_user_id})",
            f"Notification chat_id={notification_chat_id} message_id={notification_message_id}",
            "",
            "Original notification:",
            "---",
            body,
            "---",
            "",
            "Reason:",
            reason,
        ]
    )


async def report_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user or not update.effective_chat:
        return ConversationHandler.END

    if not _is_notification_chat(update):
        return ConversationHandler.END

    reply = update.message.reply_to_message
    if reply is None:
        await update.message.reply_text(
            "Reply to the notification you want to report, then send /report."
        )
        return ConversationHandler.END

    context.user_data[_REPORT_MSG_ID_KEY] = int(reply.message_id)
    context.user_data[_REPORT_MSG_TEXT_KEY] = _message_body(reply)
    context.user_data[_REPORT_CHAT_ID_KEY] = int(update.effective_chat.id)

    await update.message.reply_text("What was wrong with this notification?")
    return REPORT_REASON


async def report_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user or not update.effective_chat:
        return ConversationHandler.END

    reason = (update.message.text or "").strip()
    if not reason:
        await update.message.reply_text("Please describe what was wrong.")
        return REPORT_REASON

    msg_id = context.user_data.get(_REPORT_MSG_ID_KEY)
    msg_text = context.user_data.get(_REPORT_MSG_TEXT_KEY, "")
    notif_chat_id = context.user_data.get(_REPORT_CHAT_ID_KEY)
    if msg_id is None or notif_chat_id is None:
        await update.message.reply_text("Report session expired. Reply with /report again.")
        return ConversationHandler.END

    user = update.effective_user
    ticket = format_report_ticket(
        reporter_username=user.username,
        reporter_user_id=int(user.id),
        notification_chat_id=int(notif_chat_id),
        notification_message_id=int(msg_id),
        notification_text=str(msg_text),
        reason=reason,
    )

    dm_ok = True
    try:
        await context.bot.send_message(
            chat_id=notification_report_to_user_id(),
            text=ticket[:4096],
        )
    except Exception:
        dm_ok = False
        logger.warning(
            "notification report: DM to report recipient failed reporter_id=%s msg_id=%s",
            user.id,
            msg_id,
            exc_info=True,
        )

    for key in (_REPORT_MSG_ID_KEY, _REPORT_MSG_TEXT_KEY, _REPORT_CHAT_ID_KEY):
        context.user_data.pop(key, None)

    if dm_ok:
        await update.message.reply_text("Report submitted. Thanks!")
    else:
        await update.message.reply_text(
            "Report recorded but could not DM @jz034 — they may need to /start this bot."
        )
    return ConversationHandler.END


async def report_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for key in (_REPORT_MSG_ID_KEY, _REPORT_MSG_TEXT_KEY, _REPORT_CHAT_ID_KEY):
        context.user_data.pop(key, None)
    if update.message:
        await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def get_report_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("report", report_entry, filters=filters.REPLY),
        ],
        states={
            REPORT_REASON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, report_reason),
            ],
        },
        fallbacks=[CommandHandler("cancel", report_cancel)],
        conversation_timeout=300,
        name="notification_report",
    )
