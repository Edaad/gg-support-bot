"""Handle bot being added to a group — link group to club and send welcome message."""

from telegram import Update
from telegram.ext import ContextTypes

from bot.services.club import set_group_club, get_club_welcome


def _bot_was_added(update: Update) -> bool:
    if not update.my_chat_member:
        return False
    old = update.my_chat_member.old_chat_member.status
    new = update.my_chat_member.new_chat_member.status
    return new == "member" and old in ("left", "kicked")


async def on_my_chat_member_updated(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.effective_user:
        return
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    if not _bot_was_added(update):
        return

    chat_id = update.effective_chat.id
    adder_uid = update.effective_user.id
    club_id = set_group_club(chat_id, adder_uid)

    if club_id is None:
        print(f"User {adder_uid} added bot to group {chat_id} but has no club")
        return

    welcome = get_club_welcome(club_id)
    if not welcome:
        return

    try:
        if welcome["type"] == "photo" and welcome.get("file_id"):
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=welcome["file_id"],
                caption=welcome.get("caption") or None,
            )
        elif welcome.get("text"):
            text = welcome["text"]
            chunk = 4096
            for i in range(0, len(text), chunk):
                await context.bot.send_message(chat_id=chat_id, text=text[i : i + chunk])
    except Exception as e:
        print(f"Failed to send welcome to {chat_id}: {e}")
