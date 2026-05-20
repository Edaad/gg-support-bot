"""Admin commands to bypass cashout cooldown for a support group."""

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_USER_IDS
from bot.services.club import (
    get_club_for_chat,
    is_club_staff,
    grant_bypass,
)


def _is_admin_for_club(user_id: int, club_id: int) -> bool:
    return user_id in ADMIN_USER_IDS or is_club_staff(user_id, club_id)


async def bypass_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Grant a one-time cashout cooldown bypass for this support group."""
    await _handle_bypass(update, "one_time")


async def bypass_permanent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Grant a permanent cashout cooldown bypass for this support group."""
    await _handle_bypass(update, "permanent")


async def _handle_bypass(update: Update, bypass_type: str) -> None:
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use this command in a club group chat.")
        return

    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        await update.message.reply_text("This group isn't linked to a club.")
        return

    admin_id = update.effective_user.id
    if not _is_admin_for_club(admin_id, club_id):
        return

    grant_bypass(club_id, chat.id, bypass_type)

    if bypass_type == "one_time":
        await update.message.reply_text(
            "One-time cashout bypass granted for this group. "
            "The next /cashout here will skip the cooldown."
        )
    else:
        await update.message.reply_text(
            "Permanent cashout bypass granted for this group. "
            "Cooldown rules will never apply here."
        )
