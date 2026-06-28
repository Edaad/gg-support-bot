"""Group command: /cashapp — create per-group Stripe Checkout link (Cash App deposits over $100)."""

from __future__ import annotations

from decimal import Decimal

from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.group_checkout_commands import (
    deposit_method_id_for_slug,
    reject_group_checkout_in_dm,
    send_group_stripe_checkout,
)
from bot.services.club import get_club_for_chat

_CASHAPP_CHECKOUT_MIN = Decimal("101")
_CASHAPP_CHECKOUT_MAX = Decimal("2000")

_CASHAPP_PAY_TEXT = (
    "$2000 MAXIMUM\n\n"
    "For Cashapp ONLY: {{hyperlink}}\n\n"
    "Anything besides cashapp will be refunded right away.\n\n"
    "• Once sent, please inform us, and an agent will confirm the transaction "
    "and add your chips within 2 minutes!\n\n"
    "• Just post a screenshot of your transaction, and it will be credited to your account!"
)


async def cashapp_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    method_id = deposit_method_id_for_slug(int(club_id), "cashapp")

    await send_group_stripe_checkout(
        update,
        context,
        club_id=int(club_id),
        method_slug="cashapp",
        display_name="/cashapp",
        intro_text="Deposit request via Cash App (Stripe checkout, $101–$2,000)",
        pay_text_html=_CASHAPP_PAY_TEXT,
        pay_text_plain=_CASHAPP_PAY_TEXT,
        payment_method_id=method_id,
        checkout_min_usd=_CASHAPP_CHECKOUT_MIN,
        checkout_max_usd=_CASHAPP_CHECKOUT_MAX,
    )
