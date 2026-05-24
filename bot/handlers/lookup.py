"""Admin /lookup: resolve Telegram group chat id from stored group title."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_USER_IDS
from bot.services.club import find_group_chat_id_by_name, get_club_for_chat
from bot.services.player_details import parse_tracking_title, resolve_club_id_from_shorthand

_USAGE = (
    "Usage: /lookup gc <group title>\n"
    "Example: /lookup gc RT / 2427-3267 / Samin"
)


def _parse_title_args(args: list[str]) -> str:
    rest = list(args)
    if rest and rest[0].lower() == "gc":
        rest = rest[1:]
    return " ".join(rest).strip()


def _resolve_club_id_for_lookup(update: Update, title: str) -> int | None:
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        club_id = get_club_for_chat(chat.id)
        if club_id is not None:
            return int(club_id)

    parsed = parse_tracking_title(title)
    if not parsed:
        return None
    shorthand, _ = parsed
    club_id = resolve_club_id_from_shorthand(shorthand)
    return int(club_id) if club_id else None


async def lookup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if update.effective_user.id not in ADMIN_USER_IDS:
        return

    title = _parse_title_args(context.args or [])
    if not title:
        await update.message.reply_text(_USAGE)
        return

    club_id = _resolve_club_id_for_lookup(update, title)
    if club_id is None:
        await update.message.reply_text(
            "Could not determine club. Run in a linked group or use a title with "
            "club shorthand (e.g. RT / 2427-3267 / Samin)."
        )
        return

    chat_id = find_group_chat_id_by_name(club_id, title)
    if chat_id is None:
        await update.message.reply_text(
            f"No linked group found with title:\n{title}"
        )
        return

    await update.message.reply_text(f"Group chat id: {chat_id}")
