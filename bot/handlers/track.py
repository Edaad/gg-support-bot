"""Group title tracking for player_details.

Triggers:
- Group title change (NEW_CHAT_TITLE): silent on invalid format; success message on bind;
  MTProto contact sync when enabled (see mtproto_track_contact).
- /track: same bind logic; replies with invalid format on failure; contact sync on successful bind.
- /info: show current bindings; schedules MTProto player contact sync when enabled.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_USER_IDS
from bot.services.club import get_club_for_chat, get_group_name, is_club_staff, update_group_name
from bot.services.mtproto_track_contact import schedule_save_player_contact_named_group
from bot.services.player_details import (
    parse_tracking_title,
    resolve_club_id_from_shorthand,
    bind_chat_from_title,
    BindResult,
    get_bound_players,
    gg_player_id_from_title,
    is_same_club_player_conflict_message,
    override_chat_for_player,
)


_EXPECTED = "Expected: SHORTHAND / GGPLAYERID / anything (example: GTO / 8190-5287 / ThePirate343)"


def _can_manage_player_tracking(user_id: int, club_id: int) -> bool:
    return user_id in ADMIN_USER_IDS or is_club_staff(user_id, club_id)


def _club_id_for_contact_sync(chat) -> int | None:
    """Resolve dashboard club_id from title shorthand or groups link (for MTProto contact save)."""
    parsed = parse_tracking_title(chat.title or "")
    if parsed:
        shorthand, _ = parsed
        cid = resolve_club_id_from_shorthand(shorthand)
        if cid:
            return cid
    return get_club_for_chat(chat.id)


async def _bind_from_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[bool, str | None]:
    """Try to parse + bind. Returns (success, gg_player_id_if_success)."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return False, None
    res = bind_chat_from_title(chat_id=chat.id, title=chat.title)
    return (True, res.gg_player_id) if res.ok and res.gg_player_id else (False, None)


def _bind_result(update: Update) -> BindResult:
    chat = update.effective_chat
    if not chat:
        return BindResult(ok=False, error="No chat.")
    return bind_chat_from_title(chat_id=chat.id, title=chat.title)


async def on_new_chat_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto-bind on group title change. Silent on invalid."""
    chat = update.effective_chat
    previous_gg_player_id = None
    if chat:
        previous_gg_player_id = gg_player_id_from_title(get_group_name(chat.id))
        update_group_name(chat.id, chat.title)

    res = _bind_result(update)
    if not res.ok:
        # Silent only for invalid format. For same-club conflicts, notify.
        if (
            res.error
            and is_same_club_player_conflict_message(res.error)
            and context.bot
            and update.effective_chat
        ):
            await context.bot.send_message(chat_id=update.effective_chat.id, text=res.error)
        return
    if context.bot and update.effective_chat and res.gg_player_id:
        chat = update.effective_chat
        player_id_changed = res.gg_player_id != previous_gg_player_id
        if player_id_changed:
            await context.bot.send_message(
                chat_id=chat.id,
                text=(
                    "Thank you for playing at our club!!\n"
                    f"Player ID: {res.gg_player_id}"
                ),
            )
        club_id = _club_id_for_contact_sync(chat)
        schedule_save_player_contact_named_group(
            chat_id=chat.id,
            club_id=club_id,
            chat_title=chat.title,
        )


async def override_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Force this group to be the tracked chat for a player id (replaces other links)."""
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use /override in a club group chat.")
        return

    club_id = get_club_for_chat(chat.id)
    if not club_id:
        await update.message.reply_text(
            "This group is not linked to a club yet. Add the bot as a club owner first."
        )
        return
    if not _can_manage_player_tracking(update.effective_user.id, club_id):
        return

    args = (context.args or [])
    gg_player_id = args[0].strip() if args else gg_player_id_from_title(chat.title)
    if not gg_player_id:
        await update.message.reply_text(
            "Usage: /override PLAYER_ID\n"
            "Example: /override 1111-2222\n\n"
            f"Player id can also be taken from the group title. {_EXPECTED}"
        )
        return

    res = override_chat_for_player(
        club_id=club_id,
        gg_player_id=gg_player_id,
        chat_id=chat.id,
    )
    if not res.ok:
        await update.message.reply_text(res.error or "Override failed.")
        return

    update_group_name(chat.id, chat.title)
    lines = [
        f"This chat is now the tracked group for player ID {res.gg_player_id}.",
    ]
    if res.previous_chat_ids:
        prev = ", ".join(str(c) for c in res.previous_chat_ids)
        lines.append(f"Replaced previous linked chat id(s) for this player: {prev}")
    await update.message.reply_text("\n".join(lines))

    schedule_save_player_contact_named_group(
        chat_id=chat.id,
        club_id=club_id,
        chat_title=chat.title,
    )


async def track_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual bind command. Replies with invalid format if not parsable/resolvable."""
    if not update.message or not update.effective_chat:
        return
    if not update.effective_user or update.effective_user.id not in ADMIN_USER_IDS:
        return
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use /track in a club group chat.")
        return
    res = _bind_result(update)
    if res.ok and res.gg_player_id:
        update_group_name(chat.id, chat.title)
        await update.message.reply_text(
            f"Successfully tracking player id: {res.gg_player_id}"
        )
        club_id = _club_id_for_contact_sync(chat)
        schedule_save_player_contact_named_group(
            chat_id=chat.id,
            club_id=club_id,
            chat_title=chat.title,
        )
    else:
        if res.error and is_same_club_player_conflict_message(res.error):
            await update.message.reply_text(res.error)
        else:
            await update.message.reply_text(f"Invalid group name format. {_EXPECTED}")


async def info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show what this chat is currently bound to; schedules MTProto contact sync when enabled."""
    if not update.message or not update.effective_chat:
        return
    if not update.effective_user or update.effective_user.id not in ADMIN_USER_IDS:
        return
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use /info in a club group chat.")
        return

    club_id = _club_id_for_contact_sync(chat)
    if not club_id:
        await update.message.reply_text("Not bound.")
        return

    schedule_save_player_contact_named_group(
        chat_id=chat.id,
        club_id=club_id,
        chat_title=chat.title,
    )

    players = get_bound_players(club_id=club_id, chat_id=chat.id)
    if not players:
        await update.message.reply_text("Not bound.")
        return

    if len(players) == 1:
        await update.message.reply_text(f"Tracking player ID: {players[0]}")
    else:
        joined = ", ".join(players)
        await update.message.reply_text(f"Tracking player IDs: {joined}")

