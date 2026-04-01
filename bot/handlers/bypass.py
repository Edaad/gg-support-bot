"""Admin commands to bypass cashout cooldown for specific players."""

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
    """Grant a one-time cashout cooldown bypass. Admin must reply to the target player's message."""
    await _handle_bypass(update, "one_time")


async def bypass_permanent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Grant a permanent cashout cooldown bypass. Admin must reply to the target player's message."""
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

    reply = update.message.reply_to_message
    if not reply or not reply.from_user:
        await update.message.reply_text(
            "Reply to a message from the player you want to bypass the cooldown for."
        )
        return

    target_user = reply.from_user
    if target_user.is_bot:
        await update.message.reply_text("Cannot bypass cooldown for a bot.")
        return

    grant_bypass(club_id, target_user.id, bypass_type)

    display_name = target_user.first_name or str(target_user.id)

    if bypass_type == "one_time":
        await update.message.reply_text(
            f"One-time cashout bypass granted for {display_name}. "
            f"Their next /cashout will skip the cooldown."
        )
    else:
        await update.message.reply_text(
            f"Permanent cashout bypass granted for {display_name}. "
            f"Cooldown rules will never apply to them in this club."
        )
