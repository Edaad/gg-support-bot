"""Staff commands to manually stage inactive support megagroups for outreach."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from club_gc_settings import gc_mtproto_operator_telegram_user_ids
from config import ADMIN_USER_IDS
from bot.services.inactive_group_outreach_staging import (
    format_stage_success_message,
    format_staged_list_message,
    list_staged_groups,
    lookup_outreach_row_title,
    resolve_club_key_for_chat,
    stage_inactive_group,
    stage_inactive_group_by_row_id,
    unstage_inactive_group,
)
from bot.services.club import get_group_name

logger = logging.getLogger(__name__)


def _can_use_inactive_outreach_stage(user_id: int) -> bool:
    if user_id in ADMIN_USER_IDS:
        return True
    return user_id in gc_mtproto_operator_telegram_user_ids()


def _parse_command_args(text: str | None) -> tuple[str, list[str]]:
    parts = (text or "").strip().split(maxsplit=1)
    args_str = parts[1].strip() if len(parts) > 1 else ""
    tokens = args_str.split() if args_str else []
    return args_str, tokens


async def _resolve_target_from_args(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    args_str: str,
    tokens: list[str],
) -> tuple[int | None, str | None, int | None, str | None]:
    """Return (chat_id, title, row_id, note)."""

    message = update.message
    chat = update.effective_chat
    if not message or not chat:
        return None, None, None, None

    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        return int(chat.id), (chat.title or "").strip() or "(untitled)", None, args_str or None

    if not tokens:
        return None, None, None, "Usage: /stageinactive <chat_id> [note]\nOr: /stageinactive row <id> [note]"

    if tokens[0].lower() == "row":
        if len(tokens) < 2 or not tokens[1].lstrip("-").isdigit():
            return None, None, None, "Usage: /stageinactive row <outreach_row_id> [note]"
        row_id = int(tokens[1])
        note = " ".join(tokens[2:]).strip() or None
        return None, None, row_id, note

    if not tokens[0].lstrip("-").isdigit():
        return None, None, None, "Usage: /stageinactive <chat_id> [note]"

    chat_id = int(tokens[0])
    note = " ".join(tokens[1:]).strip() or None
    title: str | None = None
    try:
        tg_chat = await context.bot.get_chat(chat_id)
        title = (tg_chat.title or "").strip() or None
    except Exception as exc:
        logger.warning("stageinactive: get_chat(%s) failed: %s", chat_id, type(exc).__name__)

    if not title:
        title = get_group_name(chat_id) or lookup_outreach_row_title(chat_id)

    return chat_id, title, None, note


async def _handle_stage_or_unstage(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    unstage: bool,
) -> None:
    message = update.message
    user = update.effective_user
    if not message or not user:
        return
    if not _can_use_inactive_outreach_stage(user.id):
        return

    args_str, tokens = _parse_command_args(message.text)
    chat_id, title, row_id, note_or_error = await _resolve_target_from_args(
        update,
        context,
        args_str=args_str,
        tokens=tokens,
    )
    if note_or_error and chat_id is None and row_id is None and title is None:
        await message.reply_text(note_or_error)
        return

    if row_id is not None:
        if unstage:
            result = unstage_inactive_group(row_id=row_id)
        else:
            result = stage_inactive_group_by_row_id(
                row_id=row_id,
                staged_by_user_id=user.id,
                note=note_or_error,
            )
        if not result.ok:
            await message.reply_text(result.error or "Failed.")
            return
        if unstage:
            await message.reply_text(
                f"Unstaged row {result.row_id}: {result.group_title} ({result.telegram_chat_id})"
            )
        else:
            await message.reply_text(format_stage_success_message(result))
        return

    if chat_id is None:
        await message.reply_text("Could not resolve chat.")
        return
    if not title:
        await message.reply_text(
            "Could not resolve group title. Use /stageinactive row <id> or run from inside the group."
        )
        return

    club_key = resolve_club_key_for_chat(chat_id, title)
    if not club_key:
        await message.reply_text(
            "Could not resolve club for this chat. Link the group in dashboard or use a tracking title."
        )
        return

    if unstage:
        result = unstage_inactive_group(club_key=club_key, telegram_chat_id=chat_id)
    else:
        result = stage_inactive_group(
            club_key=club_key,
            telegram_chat_id=chat_id,
            group_title=title,
            staged_by_user_id=user.id,
            note=note_or_error,
        )

    if not result.ok:
        await message.reply_text(result.error or "Failed.")
        return

    if unstage:
        await message.reply_text(
            f"Unstaged row {result.row_id}: {result.group_title} ({result.telegram_chat_id})"
        )
    else:
        await message.reply_text(format_stage_success_message(result))


async def stageinactive_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await _handle_stage_or_unstage(update, context, unstage=False)


async def unstageinactive_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await _handle_stage_or_unstage(update, context, unstage=True)


async def stagedinactive_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return
    if not _can_use_inactive_outreach_stage(user.id):
        return
    if chat.type != ChatType.PRIVATE:
        await message.reply_text("Use /stagedinactive in DM.")
        return

    _, tokens = _parse_command_args(message.text)
    club_key: str | None = None
    if tokens:
        if tokens[0] in ("round_table", "creator_club", "clubgto"):
            club_key = tokens[0]
        elif tokens[0] == "--club" and len(tokens) >= 2:
            club_key = tokens[1]

    rows = list_staged_groups(club_key=club_key, limit=50)
    await message.reply_text(format_staged_list_message(rows, club_key=club_key))
