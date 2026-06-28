"""Group command: /stripe — create Stripe Checkout link ($20–$100) without /deposit wizard."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.group_checkout_commands import (
    reject_group_checkout_in_dm,
    send_group_stripe_checkout,
)
from bot.services.club import get_club_for_chat

_STRIPE_PAY_TEXT = (
    "🚨 NO CREDIT CARDS. They will be refunded immediately\n\n"
    "• Enter your deposit amount on the checkout page ($20 minimum, $100 maximum).\n\n"
    "• Once sent, please inform us, and an agent will confirm the transaction "
    "and add your chips within 2 minutes!\n\n"
    "• Just post a screenshot of your transaction, and it will be credited to your account!\n\n"
    "{{hyperlink}}"
)


async def stripe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return

    if await reject_group_checkout_in_dm(update):
        return

    chat = update.effective_chat
    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        await update.message.reply_text(
            "This group isn't linked to a club yet. The club owner must add the bot."
        )
        return

    await send_group_stripe_checkout(
        update,
        context,
        club_id=int(club_id),
        method_slug="stripe",
        display_name="/stripe",
        intro_text="Deposit request via Stripe ($20–$100 on checkout)",
        pay_text_html=_STRIPE_PAY_TEXT,
        pay_text_plain=_STRIPE_PAY_TEXT,
    )
