"""Test-bot group command: /unbindmethod — clear all payment-method links for this chat."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_USER_IDS
from bot.runtime_config import is_test_bot_worker
from bot.services.club import get_club_for_chat, is_club_staff, update_group_name
from bot.services.payment_method_binding import (
    list_chat_method_bindings,
    unbind_chat_from_all_methods,
)

logger = logging.getLogger(__name__)


def _format_binding_summary(bindings) -> str:
    if not bindings:
        return ""
    parts = []
    for row in bindings:
        slug = row.payment_method_slug
        handle = (row.venmo_handle or "").strip()
        if handle:
            parts.append(f"{slug} ({handle})")
        else:
            parts.append(slug)
    return ", ".join(parts)


async def unbindmethod_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_test_bot_worker():
        return
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use /unbindmethod in a group chat.")
        return

    user_id = update.effective_user.id
    club_id = get_club_for_chat(chat.id)
    if user_id not in ADMIN_USER_IDS and (
        club_id is None or not is_club_staff(user_id, club_id)
    ):
        await update.message.reply_text(
            "Only club staff can run /unbindmethod in this group."
        )
        return

    if club_id is None:
        await update.message.reply_text("This group isn't linked to a club.")
        return

    if context.args:
        await update.message.reply_text(
            "/unbindmethod clears all payment-method links for this group "
            "(venmo, zelle, etc.). No method argument is needed."
        )
        return

    update_group_name(chat.id, chat.title)

    bindings_before = list_chat_method_bindings(chat.id)
    bindings_removed, attempts_cancelled = unbind_chat_from_all_methods(chat.id)

    if bindings_removed == 0 and attempts_cancelled == 0:
        await update.message.reply_text(
            "This group has no payment-method links or pending setup attempts. "
            "Nothing to unbind."
        )
        return

    cleared = _format_binding_summary(bindings_before)
    lines = ["Unbound all payment methods for this group."]
    if cleared:
        lines.append(f"Cleared: {cleared}")
    if attempts_cancelled:
        lines.append(
            f"Cancelled {attempts_cancelled} pending setup attempt"
            f"{'' if attempts_cancelled == 1 else 's'}."
        )
    lines.append(
        "The next /deposit with venmo or zelle will require linking again."
    )

    logger.info(
        "unbindmethod ok chat_id=%s club_id=%s user_id=%s bindings_removed=%s "
        "attempts_cancelled=%s",
        chat.id,
        club_id,
        user_id,
        bindings_removed,
        attempts_cancelled,
    )
    await update.message.reply_text("\n".join(lines))
