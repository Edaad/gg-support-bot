"""Admin/staff command: generate a Stripe Checkout link with no minimum for a group chat."""

import html
import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_USER_IDS
from bot.services.club import get_club_for_chat, is_club_staff
from bot.services.stripe_deposit import create_stripe_checkout_session, stripe_configured

logger = logging.getLogger(__name__)


async def teststripe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use /teststripe in a group chat.")
        return

    user_id = update.effective_user.id
    club_id = get_club_for_chat(chat.id)

    if user_id not in ADMIN_USER_IDS and (
        club_id is None or not is_club_staff(user_id, club_id)
    ):
        return

    if not stripe_configured():
        await update.message.reply_text(
            "Stripe is not configured on this bot (STRIPE_SECRET_KEY missing)."
        )
        return

    if club_id is None:
        await update.message.reply_text("This group isn't linked to a club.")
        return

    try:
        result = create_stripe_checkout_session(
            telegram_chat_id=chat.id,
            club_id=int(club_id),
            group_title=chat.title,
            no_minimum=True,
        )
    except Exception as e:
        logger.exception("teststripe: checkout creation failed chat_id=%s", chat.id)
        await update.message.reply_text(
            f"Failed to generate Stripe link: {type(e).__name__}"
        )
        return

    safe_url = html.escape(result.checkout_url, quote=True)
    try:
        await update.message.reply_text(
            f'<a href="{safe_url}">Stripe Checkout (no minimum)</a>',
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        await update.message.reply_text(result.checkout_url)

    logger.info(
        "teststripe: link sent chat_id=%s session_id=%s", chat.id, result.session_id
    )
