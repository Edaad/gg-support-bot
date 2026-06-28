"""Shared helpers for /stripe and /cashapp group-only Stripe checkout commands."""

from __future__ import annotations

import html
import logging
from decimal import Decimal
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.deposit import (
    _notify_missing_stripe_secret,
    _notify_stripe_checkout_failure,
    _stripe_error_detail,
)
from bot.services.club import get_club_for_chat, get_methods_for_amount, update_group_name
from bot.services.stripe_deposit import create_stripe_checkout_session, stripe_configured

logger = logging.getLogger(__name__)

GROUP_CHECKOUT_DM_MESSAGE = (
    "Please use this command in a group chat — it generates a unique payment link "
    "for your support group."
)

GROUP_ONLY_CHECKOUT_COMMANDS = frozenset({"stripe", "cashapp"})


def is_group_chat(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.type in ("group", "supergroup")


async def reject_group_checkout_in_dm(update: Update) -> bool:
    """Reply in DM and return True when the update is not from a group."""
    if not update.message or is_group_chat(update):
        return False
    await update.message.reply_text(GROUP_CHECKOUT_DM_MESSAGE)
    return True


def deposit_method_id_for_slug(club_id: int, slug: str) -> Optional[int]:
    slug = slug.strip().lower()
    methods = get_methods_for_amount(club_id, "deposit", amount=None)
    for method in methods:
        if (method.get("slug") or "").strip().lower() == slug:
            return int(method["id"])
    return None


async def send_group_stripe_checkout(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    club_id: int,
    method_slug: str,
    display_name: str,
    intro_text: str,
    pay_text_html: str,
    pay_text_plain: str,
    payment_method_id: int | None = None,
    checkout_min_usd: Decimal | float | int | str | None = None,
    checkout_max_usd: Decimal | float | int | str | None = None,
) -> None:
    if not update.message or not update.effective_chat:
        return

    chat = update.effective_chat
    update_group_name(chat.id, chat.title)

    if not stripe_configured():
        logger.warning(
            "%s cmd: STRIPE_SECRET_KEY not set chat_id=%s",
            method_slug,
            chat.id,
        )
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
            payment_method_id=payment_method_id,
            group_title=chat.title,
            checkout_min_usd=checkout_min_usd,
            checkout_max_usd=checkout_max_usd,
        )
    except Exception as e:
        err_detail = _stripe_error_detail(e)
        logger.exception(
            "%s cmd: checkout creation failed chat_id=%s club_id=%s: %s",
            method_slug,
            chat.id,
            club_id,
            err_detail,
        )
        await _notify_stripe_checkout_failure(
            context,
            club_id=int(club_id),
            chat_id=chat.id,
            group_title=chat.title,
            method_id=payment_method_id,
            method_slug=method_slug,
            display_name=display_name,
            amount=None,
            checkout_min_usd=checkout_min_usd,
            checkout_max_usd=checkout_max_usd,
            error_detail=err_detail,
        )
        await update.message.reply_text(
            "Card checkout failed to start. Please try again in a minute or contact support."
        )
        return

    await update.message.reply_text(intro_text)

    pay_html = pay_text_html.replace("{{hyperlink}}", f'<a href="{html.escape(result.checkout_url, quote=True)}">PAY HERE</a>')
    pay_plain = pay_text_plain.replace("{{hyperlink}}", result.checkout_url)
    try:
        await update.message.reply_text(
            pay_html,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        logger.warning(
            "%s cmd: HTML pay message failed, retrying plain link chat_id=%s",
            method_slug,
            chat.id,
            exc_info=True,
        )
        await update.message.reply_text(pay_plain)

    logger.info(
        "%s cmd: checkout sent chat_id=%s session_id=%s customer_id=%s",
        method_slug,
        chat.id,
        result.session_id,
        result.customer_id,
    )
