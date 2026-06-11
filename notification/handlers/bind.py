"""Reply-to-bind handler for payment notifications (Cash App, Venmo, Zelle, Crypto)."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.services.cashapp_payments import bind_cashapp_payment_from_reply
from bot.services.crypto_payments import bind_crypto_payment_from_reply
from bot.services.venmo_payments import bind_venmo_payment_from_reply
from bot.services.zelle_payments import bind_zelle_payment_from_reply
from notification.chat_id import telegram_chat_ids_match
from notification.constants import PAYMENT_NOTIFICATION_CHAT_ID_ENV
from notification.handlers._chat import notification_chat_id

logger = logging.getLogger(__name__)


async def payment_bind_reply_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    expected_chat = notification_chat_id()
    if expected_chat is None:
        logger.warning("payment bind: %s not set", PAYMENT_NOTIFICATION_CHAT_ID_ENV)
        return

    chat_id = int(update.effective_chat.id)
    if not telegram_chat_ids_match(chat_id, expected_chat):
        logger.debug(
            "payment bind: ignoring message chat_id=%s (expected %s)",
            chat_id,
            expected_chat,
        )
        return

    reply = update.message.reply_to_message
    if reply is None:
        return

    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Send the group title as your reply text.")
        return

    bind_kwargs = dict(
        notification_chat_id=expected_chat,
        notification_message_id=int(reply.message_id),
        group_title_input=title,
        bound_by_telegram_user_id=int(update.effective_user.id),
    )

    try:
        result = await bind_crypto_payment_from_reply(**bind_kwargs)
        if not result.ok:
            result = await bind_cashapp_payment_from_reply(**bind_kwargs)
        if not result.ok:
            result = await bind_zelle_payment_from_reply(**bind_kwargs)
        if not result.ok:
            result = await bind_venmo_payment_from_reply(**bind_kwargs)
    except Exception:
        logger.exception(
            "payment bind failed chat_id=%s reply_to=%s title=%r",
            chat_id,
            reply.message_id,
            title,
        )
        await update.message.reply_text(
            "Bind failed due to a server error. Check notification dyno logs."
        )
        return

    if not result.ok or result.bound_group is None:
        await update.message.reply_text(result.error or "Could not bind payment.")
        return

    group = result.bound_group
    await update.message.reply_text(
        f"Bound to {group.group_title} (chat_id {group.telegram_chat_id})"
    )
    logger.info(
        "payment bound chat_id=%s group=%r user_id=%s",
        group.telegram_chat_id,
        group.group_title,
        update.effective_user.id,
    )


async def venmo_bind_reply_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Backward-compatible alias for payment_bind_reply_handler."""
    await payment_bind_reply_handler(update, context)
