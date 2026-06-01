"""Admin /refresh: restart all Heroku dynos (DM only)."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.services.heroku_restart import get_heroku_app_name, restart_all_dynos
from config import ADMIN_USER_IDS

logger = logging.getLogger(__name__)

_USAGE = (
    "Usage: /refresh confirm\n\n"
    "Restarts all Heroku dynos (web, worker, cashier, notification). "
    "Expect ~1 minute downtime."
)


async def refresh_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not update.effective_chat:
        return

    if update.effective_user.id not in ADMIN_USER_IDS:
        return

    chat = update.effective_chat
    if chat.type != "private":
        await update.message.reply_text("/refresh is only available in DM with the bot.")
        return

    args = [a.lower() for a in (context.args or [])]
    if args != ["confirm"]:
        await update.message.reply_text(_USAGE)
        return

    uid = update.effective_user.id
    app_name = get_heroku_app_name()
    if not app_name:
        await update.message.reply_text(
            "Refresh failed: HEROKU_APP_NAME is not set on the worker."
        )
        return

    await update.message.reply_text(
        f"Restarting all Heroku dynos for {app_name}… (~1 min downtime)"
    )

    try:
        await restart_all_dynos(triggered_by_user_id=uid)
    except RuntimeError as e:
        logger.warning("refresh failed for uid=%s: %s", uid, e)
        await update.message.reply_text(f"Refresh failed: {e}")
