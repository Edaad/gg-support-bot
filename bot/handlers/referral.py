"""Player /referral_link and deep-link hop DMs."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bot.handlers.flow_cancel import ACTIVE_FLOW_KEY
from bot.services.club import get_club_for_chat
from bot.services.player_details import gg_player_id_from_title
from bot.services.referrals import (
    UNTITLED_GROUP_ERROR,
    build_referral_url,
    ensure_referral_link,
    format_referral_link_message,
    format_my_referrals_messages,
    get_credited_referral_player_ids,
    handle_start_payload,
    hop_message,
    is_referral_start_payload,
    pending_hop_for_user,
)
from bot.services.support_group_chats import (
    fetch_support_group_chat_by_telegram_chat_id,
)

logger = logging.getLogger(__name__)

_BLOCKING_DM_FLOWS = frozenset(
    {
        "bonus",
        "inactive_outreach_send",
        "deposit_access",
        "issue_report",
        "support_note",
    }
)


async def referral_link_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Post the group's unique referral deep link. Silent outside support groups."""
    if not update.message or not update.effective_chat:
        return
    chat = update.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    sgc = fetch_support_group_chat_by_telegram_chat_id(chat.id)
    if sgc is None:
        return

    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        return

    title = chat.title or sgc.telegram_chat_title or ""
    player_id = gg_player_id_from_title(title)
    if not player_id:
        await update.message.reply_text(UNTITLED_GROUP_ERROR)
        return

    link = ensure_referral_link(
        club_id=int(club_id),
        referrer_chat_id=int(chat.id),
        referrer_gg_player_id=player_id,
    )
    if link is None:
        await update.message.reply_text("Could not create a referral link. Try again.")
        return

    bot_username = ""
    if context.bot and context.bot.username:
        bot_username = context.bot.username
    else:
        try:
            me = await context.bot.get_me()
            bot_username = (me.username or "") if me else ""
        except Exception:
            logger.warning("referral_link: get_me failed chat_id=%s", chat.id)

    if not bot_username:
        await update.message.reply_text("Could not build referral link. Try again.")
        return

    url = build_referral_url(bot_username=bot_username, code=link.code)
    await update.message.reply_text(format_referral_link_message(url))


async def my_referrals_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """List credited referrals for this titled support group."""
    if not update.message or not update.effective_chat:
        return
    chat = update.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    sgc = fetch_support_group_chat_by_telegram_chat_id(chat.id)
    if sgc is None:
        return

    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        return

    title = chat.title or sgc.telegram_chat_title or ""
    if not gg_player_id_from_title(title):
        await update.message.reply_text(UNTITLED_GROUP_ERROR)
        return

    player_ids = get_credited_referral_player_ids(
        club_id=int(club_id),
        referrer_chat_id=int(chat.id),
    )
    for text in format_my_referrals_messages(player_ids):
        await update.message.reply_text(text)


async def maybe_handle_referral_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """If /start has a ref_ payload (or pending hop), reply and return True."""
    if not update.message or not update.effective_chat or not update.effective_user:
        return False
    if update.effective_chat.type != ChatType.PRIVATE:
        return False

    args = context.args or []
    payload = args[0].strip() if args else ""

    if is_referral_start_payload(payload):
        result = handle_start_payload(
            clicker_telegram_user_id=int(update.effective_user.id),
            code=payload,
        )
        text = result.text
        if result.kind in ("hop", "existing") and result.club_id is not None:
            from bot.services.mtproto_dm_gc_listener import (
                run_referral_gc_for_bot_user,
            )

            automated_text = await run_referral_gc_for_bot_user(
                club_id=int(result.club_id),
                player_telegram_user_id=int(update.effective_user.id),
                player_username=update.effective_user.username,
            )
            text = automated_text or hop_message(int(result.club_id))
        await update.message.reply_text(text)
        return True

    pending = pending_hop_for_user(int(update.effective_user.id))
    if pending is not None:
        await update.message.reply_text(pending.text)
        return True

    return False


async def referral_hop_dm_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Repeat hop copy while the clicker has a pending attribution and no group yet."""
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    if update.effective_chat.type != ChatType.PRIVATE:
        return

    active = context.user_data.get(ACTIVE_FLOW_KEY)
    if active in _BLOCKING_DM_FLOWS:
        return
    if context.user_data.get("bonus_step"):
        return

    pending = pending_hop_for_user(int(update.effective_user.id))
    if pending is None:
        return

    await update.message.reply_text(pending.text)
    raise ApplicationHandlerStop()
