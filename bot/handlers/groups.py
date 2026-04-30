"""Handle bot being added to a group — link group to club and send welcome message.

Also provides auto-link logic: when the bot receives any message in an unlinked
group it queries the group's admins and, if one matches a known club owner, links
the group automatically.
"""

import logging
import time

from telegram import Update
from telegram.constants import ChatMemberStatus as CMS
from telegram.ext import ContextTypes

from bot.services.club import (
    set_group_club,
    get_club_welcome,
    get_club_for_chat,
    get_club_by_id,
    is_group_linked,
    try_link_group_by_admin,
    update_group_name,
)
from bot.services.player_details import bind_chat_from_title, is_same_club_player_conflict_message

logger = logging.getLogger(__name__)

# Keep a small in-memory set so we only attempt auto-link once per chat per
# bot process lifetime (avoids calling get_chat_administrators on every message).
_auto_link_attempted: set[int] = set()

# Shown whenever a human joins a group already linked to a club (not the bot-added welcome).
MEMBER_JOIN_INTRO_TEMPLATE = (
    "👋 Hey, glad to have you at {club_name}!\n\n"
    "Please use your group chat for all club inquiries. "
    "We will only ever respond here.\n\n"
    "Please USE THE FOLLOWING COMMANDS on this groupchat to request deposits and cashouts:"
)

# Supergroups often emit ``chat_member`` instead of ``new_chat_members``; throttle avoids double texts.
JOIN_INTRO_THROTTLE_S = 2.0
_join_intro_sent_at: dict[int, float] = {}


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
    club_id = set_group_club(chat_id, adder_uid, chat_title=update.effective_chat.title)

    _auto_link_attempted.discard(chat_id)

    if club_id is None:
        print(f"User {adder_uid} added bot to group {chat_id} but has no club")
        return

    welcome = get_club_welcome(club_id)
    if welcome:
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

    # Also attempt player_details binding from the group title (after welcome).
    # Silent on invalid format; show explicit conflict errors.
    res = bind_chat_from_title(chat_id=chat_id, title=update.effective_chat.title)
    if context.bot:
        try:
            if res.ok and res.gg_player_id:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "Thank you for playing at our club!!\n"
                        f"\nPlayer ID: {res.gg_player_id}"
                    ),
                )
            elif res.error and is_same_club_player_conflict_message(res.error):
                await context.bot.send_message(chat_id=chat_id, text=res.error)
        except Exception:
            pass


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
        update_group_name(chat_id, chat.title)
        return

    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        admin_ids = [m.user.id for m in admins if not m.user.is_bot]
    except Exception as exc:
        logger.warning("auto_link_group: could not fetch admins for %s: %s", chat_id, exc)
        return

    club_id = try_link_group_by_admin(chat_id, admin_ids, chat_title=chat.title)
    if club_id:
        logger.info("auto_link_group: linked chat %s to club %s", chat_id, club_id)
    else:
        logger.debug("auto_link_group: no matching club owner found among admins of %s", chat_id)


async def _maybe_send_member_join_intro(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Standard club intro after a human joins — only when this chat is linked to a dashboard club."""

    club_id = get_club_for_chat(chat_id)
    if not club_id:
        return

    now = time.monotonic()
    last = _join_intro_sent_at.get(chat_id)
    if last is not None and (now - last) < JOIN_INTRO_THROTTLE_S:


        return
    _join_intro_sent_at[chat_id] = now

    club = get_club_by_id(club_id)
    club_name = (club.name or "our club").strip() if club else "our club"
    text = MEMBER_JOIN_INTRO_TEMPLATE.format(club_name=club_name)


    try:
        await context.bot.send_message(chat_id=chat_id, text=text)


    except Exception as e:


        logger.warning("member_join_intro: send_message failed chat_id=%s: %s", chat_id, e)


async def on_new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:


    chat = update.effective_chat
    msg = update.message

    if not chat or chat.type not in ("group", "supergroup"):
        return


    if not msg or not msg.new_chat_members:
        return

    humans = [u for u in msg.new_chat_members if not u.is_bot]
    if not humans:


        return

    await _maybe_send_member_join_intro(context, chat.id)



async def on_other_chat_member_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:


    """Megagroups / supergroups: join notifications as ``chat_member`` updates."""

    cu = update.chat_member
    chat = update.effective_chat
    if not cu or not chat or chat.type not in ("group", "supergroup"):
        return



    if cu.new_chat_member.user.is_bot:


        return

    old = cu.old_chat_member.status
    nw = cu.new_chat_member.status
    # User entered the chat (invite link / add / returning after leave).
    if old not in (CMS.LEFT, CMS.BANNED) or nw in (CMS.LEFT, CMS.BANNED):
        return

    await _maybe_send_member_join_intro(context, chat.id)
