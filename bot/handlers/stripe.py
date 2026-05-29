"""Group command: /stripe — create Stripe Checkout link ($20–$100) without /deposit wizard."""

from __future__ import annotations

import html
import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.deposit import (
    _notify_missing_stripe_secret,
    _notify_stripe_checkout_failure,
    _stripe_error_detail,
)
from bot.services.club import get_club_for_chat, update_group_name
from bot.services.stripe_deposit import create_stripe_checkout_session, stripe_configured

logger = logging.getLogger(__name__)


async def stripe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use /stripe in a group chat.")
        return

    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        await update.message.reply_text(
            "This group isn't linked to a club yet. The club owner must add the bot."
        )
        return

    update_group_name(chat.id, chat.title)

    if not stripe_configured():
        logger.warning("stripe cmd: STRIPE_SECRET_KEY not set chat_id=%s", chat.id)
        await _notify_missing_stripe_secret(context, int(club_id))
        await update.message.reply_text(
            "Card checkout is not available right now (Stripe not configured on the bot). "
            "Please contact support."
        )
        return

    try:
        result = create_stripe_checkout_session(
            telegram_chat_id=chat.id,
            club_id=int(club_id),
            group_title=chat.title,
        )
    except Exception as e:
        err_detail = _stripe_error_detail(e)
        logger.exception(
            "stripe cmd: checkout creation failed chat_id=%s club_id=%s: %s",
            chat.id,
            club_id,
            err_detail,
        )
        await _notify_stripe_checkout_failure(
            context,
            club_id=int(club_id),
            chat_id=chat.id,
            group_title=chat.title,
            method_id=None,
            method_slug="stripe",
            display_name="/stripe",
            amount=None,
            checkout_min_usd=None,
            checkout_max_usd=None,
            error_detail=err_detail,
        )
        await update.message.reply_text(
            "Card checkout failed to start. Please try again in a minute or contact support."
        )
        return

    await update.message.reply_text(
        "Deposit request via Stripe ($20–$100 on checkout)"
    )

    safe_url = html.escape(result.checkout_url, quote=True)
    pay_text = (
        "🚨 NO CREDIT CARDS. They will be refunded immediately\n\n"
        "• Enter your deposit amount on the checkout page ($20 minimum, $100 maximum).\n\n"
        "• Once sent, please inform us, and an agent will confirm the transaction and add your chips within 2 minutes!\n\n"
        "• Just post a screenshot of your transaction, and it will be credited to your account!\n\n"
        f'<a href="{safe_url}">PAY HERE</a>'
    )
    pay_text_plain = (
        "🚨 NO CREDIT CARDS. They will be refunded immediately\n\n"
        "• Enter your deposit amount on the checkout page ($20 minimum, $100 maximum).\n\n"
        "• Once sent, please inform us, and an agent will confirm the transaction and add your chips within 2 minutes!\n\n"
        "• Just post a screenshot of your transaction, and it will be credited to your account!\n\n"
        f"{result.checkout_url}"
    )
    try:
        await update.message.reply_text(
            pay_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        logger.warning(
            "stripe cmd: HTML pay message failed, retrying plain link chat_id=%s",
            chat.id,
            exc_info=True,
        )
        await update.message.reply_text(pay_text_plain)

    logger.info(
        "stripe cmd: checkout sent chat_id=%s session_id=%s customer_id=%s",
        chat.id,
        result.session_id,
        result.customer_id,
    )
