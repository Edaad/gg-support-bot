"""Handle bot being added to a group — link group to club and send welcome message.

Also provides auto-link logic: when the bot receives any message in an unlinked
group it queries the group's admins and, if one matches a known club owner, links
the group automatically.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.services.club import (
    set_group_club,
    get_club_welcome,
    is_group_linked,
    try_link_group_by_admin,
)

logger = logging.getLogger(__name__)

# Keep a small in-memory set so we only attempt auto-link once per chat per
# bot process lifetime (avoids calling get_chat_administrators on every message).
_auto_link_attempted: set[int] = set()


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

    _auto_link_attempted.discard(chat_id)

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


async def auto_link_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silently try to link an unlinked group when any message arrives.

    Works by fetching the group's admin list from Telegram and checking each
    admin against known club owners / linked accounts.  Only runs once per
    chat per process lifetime.
    """
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return

    chat_id = chat.id

    if chat_id in _auto_link_attempted:
        return
    _auto_link_attempted.add(chat_id)

    if is_group_linked(chat_id):
        return

    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        admin_ids = [m.user.id for m in admins if not m.user.is_bot]
    except Exception as exc:
        logger.warning("auto_link_group: could not fetch admins for %s: %s", chat_id, exc)
        return

    club_id = try_link_group_by_admin(chat_id, admin_ids)
    if club_id:
        logger.info("auto_link_group: linked chat %s to club %s", chat_id, club_id)
    else:
        logger.debug("auto_link_group: no matching club owner found among admins of %s", chat_id)
