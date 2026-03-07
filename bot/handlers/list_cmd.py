from telegram import Update
from telegram.ext import ContextTypes

from bot.services.club import get_club_for_chat, get_club_list_content, get_club_id_for_telegram_user


async def _reply_long(message, text: str):
    chunk = 4096
    for i in range(0, len(text), chunk):
        await message.reply_text(text[i : i + chunk])


async def list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    chat = update.effective_chat
    uid = update.effective_user.id

    if chat.type in ("group", "supergroup"):
        club_id = get_club_for_chat(chat.id)
        if club_id is None:
            await update.message.reply_text(
                "This group isn't linked to a club. The club owner must add the bot."
            )
            return
    else:
        club_id = get_club_id_for_telegram_user(uid)
        if club_id is None:
            await update.message.reply_text("You don't have a club set up yet.")
            return

    data = get_club_list_content(club_id)
    if not data:
        await update.message.reply_text("No list has been set for this club.")
        return

    if data["type"] == "photo" and data.get("file_id"):
        await update.message.reply_photo(
            photo=data["file_id"], caption=data.get("caption") or None
        )
    elif data.get("text"):
        await _reply_long(update.message, data["text"])
    else:
        await update.message.reply_text("No list content available.")
