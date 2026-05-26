"""Admin /findgc: resolve stored group title from a Telegram group chat id."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_USER_IDS
from bot.services.club import get_group_title_for_chat

_USAGE = (
    "Usage: /findgc <chat_id>\n"
    "Example: /findgc -1001234567890\n"
    "Example: /findgc tg_gc_id -1001234567890"
)


def _parse_chat_id_arg(args: list[str]) -> int | None:
    rest = list(args)
    if rest and rest[0].lower() in ("tg_gc_id", "gc_id", "chat_id"):
        rest = rest[1:]
    if not rest:
        return None
    raw = rest[0].strip()
    try:
        return int(raw)
    except ValueError:
        return None


async def findgc_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if update.effective_user.id not in ADMIN_USER_IDS:
        return

    chat_id = _parse_chat_id_arg(context.args or [])
    if chat_id is None:
        await update.message.reply_text(_USAGE)
        return

    title, _club_id = get_group_title_for_chat(chat_id)
    if not title:
        await update.message.reply_text(
            f"No stored group title found for chat id {chat_id}.\n"
            "The group may not be linked in the dashboard, or only exists on Telegram."
        )
        return

    await update.message.reply_text(
        f"Group: {title}\nChat ID: {chat_id}"
    )
