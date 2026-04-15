"""Group title tracking for player_details.

Triggers:
- Group title change (NEW_CHAT_TITLE): silent on invalid format; success message on bind.
- /track: same bind logic; replies with invalid format on failure.
- /info: show current bindings for this chat (or not bound).
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_USER_IDS
from bot.services.club import get_club_for_chat
from bot.services.player_details import (
    parse_tracking_title,
    resolve_club_id_from_shorthand,
    bind_chat_from_title,
    get_bound_players,
)


_EXPECTED = "Expected: SHORTHAND / GGPLAYERID / anything (example: GTO / 8190-5287 / ThePirate343)"


async def _bind_from_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[bool, str | None]:
    """Try to parse + bind. Returns (success, gg_player_id_if_success)."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return False, None
    gg_player_id = bind_chat_from_title(chat_id=chat.id, title=chat.title)
    return (True, gg_player_id) if gg_player_id else (False, None)


async def on_new_chat_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto-bind on group title change. Silent on invalid."""
    ok, gg = await _bind_from_title(update, context)
    if not ok or not gg:
        return
    if context.bot and update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Successfully tracking player id: {gg}",
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
    ok, gg = await _bind_from_title(update, context)
    if ok and gg:
        await update.message.reply_text(f"Successfully tracking player id: {gg}")
    else:
        await update.message.reply_text(f"Invalid group name format. {_EXPECTED}")


async def info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show what this chat is currently bound to (player ids) for the resolved club."""
    if not update.message or not update.effective_chat:
        return
    if not update.effective_user or update.effective_user.id not in ADMIN_USER_IDS:
        return
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use /info in a club group chat.")
        return

    # Prefer club resolved from title shorthand; fallback to DB group->club mapping.
    club_id = None
    parsed = parse_tracking_title(chat.title or "")
    if parsed:
        shorthand, _ = parsed
        club_id = resolve_club_id_from_shorthand(shorthand)
    if not club_id:
        club_id = get_club_for_chat(chat.id)

    if not club_id:
        await update.message.reply_text("Not bound.")
        return

    players = get_bound_players(club_id=club_id, chat_id=chat.id)
    if not players:
        await update.message.reply_text("Not bound.")
        return

    if len(players) == 1:
        await update.message.reply_text(f"Tracking player id: {players[0]}")
    else:
        joined = ", ".join(players)
        await update.message.reply_text(f"Tracking player ids: {joined}")

