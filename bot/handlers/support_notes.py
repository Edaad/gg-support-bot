"""Staff support notes: /note, /notes, /resolve for player dispute handoff."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from club_gc_settings import gc_mtproto_operator_telegram_user_ids
from config import ADMIN_USER_IDS
from bot.handlers.flow_cancel import (
    block_if_dm_flow_active,
    clear_active_flow,
    mark_active_flow,
)
from bot.services.club import get_club_for_chat, is_any_club_staff, is_club_staff
from bot.services.player_details import gg_player_id_from_title, resolve_club_id_from_shorthand
from bot.services.player_support_notes import (
    SupportNoteValidationError,
    add_note,
    club_label_for_id,
    format_note_saved,
    format_open_issues_list,
    format_player_note_history,
    format_resolve_result,
    get_player_note_history,
    list_open_issues,
    lookup_club_ids_for_player,
    resolve_issues_for_player,
    validate_gg_player_id,
)
from db.connection import get_db

logger = logging.getLogger(__name__)

PENDING_KEY = "support_note_pending"

(
    NOTE_PLAYER_ID,
    NOTE_CLUB,
    NOTE_SITUATION,
    NOTE_ACTIONS,
    NOTE_NEXT_STEPS,
) = range(5)

_NOTE_USER_KEYS = (
    "support_note_admin_id",
    "support_note_club_id",
    "support_note_gg_player_id",
    "support_note_situation",
    "support_note_actions",
    "support_note_source_chat_id",
)


def _can_use_support_notes(user_id: int) -> bool:
    if user_id in ADMIN_USER_IDS:
        return True
    if user_id in gc_mtproto_operator_telegram_user_ids():
        return True
    return is_any_club_staff(user_id)


async def _reply_long(message, text: str) -> None:
    chunk = 4096
    for i in range(0, len(text), chunk):
        await message.reply_text(text[i : i + chunk])


def _cleanup_note_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_active_flow(context)
    for key in _NOTE_USER_KEYS:
        context.user_data.pop(key, None)


async def _note_group_stub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message and update.effective_chat and update.effective_user

    chat = update.effective_chat
    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        return

    if not is_club_staff(update.effective_user.id, club_id):
        return

    try:
        await context.bot.delete_message(
            chat_id=chat.id,
            message_id=update.message.message_id,
        )
    except Exception:
        logger.warning(
            "support_note: could not delete /note command chat_id=%s message_id=%s",
            chat.id,
            update.message.message_id,
            exc_info=True,
        )

    gg_player_id = gg_player_id_from_title(chat.title)
    if not gg_player_id:
        logger.warning(
            "support_note: could not read player id from group title chat_id=%s title=%r",
            chat.id,
            chat.title,
        )
        return

    context.user_data[PENDING_KEY] = {
        "club_id": int(club_id),
        "gg_player_id": gg_player_id,
        "telegram_chat_id": chat.id,
    }


def _apply_pending_context(context: ContextTypes.DEFAULT_TYPE) -> bool:
    pending = context.user_data.pop(PENDING_KEY, None)
    if not pending:
        return False
    context.user_data["support_note_club_id"] = pending["club_id"]
    context.user_data["support_note_gg_player_id"] = pending["gg_player_id"]
    context.user_data["support_note_source_chat_id"] = pending.get("telegram_chat_id")
    return True


async def _begin_dm_note_flow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    gg_player_id: str | None = None,
) -> int:
    assert update.message and update.effective_user

    context.user_data["support_note_admin_id"] = update.effective_user.id
    mark_active_flow(context, "support_note")

    if _apply_pending_context(context):
        await update.message.reply_text("Situation:")
        return NOTE_SITUATION

    if gg_player_id:
        context.user_data["support_note_gg_player_id"] = gg_player_id
        with get_db() as session:
            club_ids = lookup_club_ids_for_player(session, gg_player_id)
        if len(club_ids) == 1:
            context.user_data["support_note_club_id"] = club_ids[0]
            await update.message.reply_text("Situation:")
            return NOTE_SITUATION
        if len(club_ids) > 1:
            return await _prompt_club_choice(update, club_ids)

    await update.message.reply_text("Player ID (example: 8190-5287):")
    return NOTE_PLAYER_ID


async def note_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user or not update.effective_chat:
        return ConversationHandler.END

    user_id = update.effective_user.id
    chat = update.effective_chat

    if not _can_use_support_notes(user_id):
        if chat.type == ChatType.PRIVATE:
            await update.message.reply_text("You are not allowed to use support notes.")
        return ConversationHandler.END

    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        await _note_group_stub(update, context)
        return ConversationHandler.END

    if chat.type != ChatType.PRIVATE:
        return ConversationHandler.END

    gg_player_id: str | None = None
    args = context.args or []
    if args:
        try:
            gg_player_id = validate_gg_player_id(args[0])
        except SupportNoteValidationError as exc:
            await update.message.reply_text(str(exc))
            return ConversationHandler.END

    if await block_if_dm_flow_active(update, context, starting="support_note"):
        return ConversationHandler.END

    _cleanup_note_flow(context)
    return await _begin_dm_note_flow(update, context, gg_player_id=gg_player_id)


async def _prompt_club_choice(update: Update, club_ids: list[int]) -> int:
    assert update.message
    buttons = []
    with get_db() as session:
        for club_id in club_ids:
            label = club_label_for_id(session, club_id)
            buttons.append(
                [InlineKeyboardButton(label, callback_data=f"snclub:{club_id}")]
            )
    await update.message.reply_text(
        "Which club is this note for?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return NOTE_CLUB


async def note_club_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    shorthand = (update.message.text or "").strip()
    club_id = resolve_club_id_from_shorthand(shorthand)
    if not club_id:
        await update.message.reply_text(
            "Unknown club shorthand. Try again (e.g. RT, GTO):"
        )
        return NOTE_CLUB
    context.user_data["support_note_club_id"] = int(club_id)
    await update.message.reply_text("Situation:")
    return NOTE_SITUATION


async def note_player_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    raw = (update.message.text or "").strip()
    try:
        gg_player_id = validate_gg_player_id(raw)
    except SupportNoteValidationError as exc:
        await update.message.reply_text(str(exc))
        return NOTE_PLAYER_ID

    context.user_data["support_note_gg_player_id"] = gg_player_id
    with get_db() as session:
        club_ids = lookup_club_ids_for_player(session, gg_player_id)
    if len(club_ids) == 1:
        context.user_data["support_note_club_id"] = club_ids[0]
        await update.message.reply_text("Situation:")
        return NOTE_SITUATION
    if len(club_ids) > 1:
        return await _prompt_club_choice(update, club_ids)

    await update.message.reply_text(
        "No club binding found for that player id. Enter club shorthand (e.g. RT, GTO):"
    )
    return NOTE_CLUB


async def note_club_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("snclub:"):
        return NOTE_CLUB
    await query.answer()
    try:
        club_id = int(query.data.split(":", 1)[1])
    except ValueError:
        await query.edit_message_text("Invalid club selection. Send /note to try again.")
        return ConversationHandler.END

    context.user_data["support_note_club_id"] = club_id
    await query.edit_message_text("Situation:")
    return NOTE_SITUATION


async def note_situation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Please describe the situation.")
        return NOTE_SITUATION
    context.user_data["support_note_situation"] = text
    await update.message.reply_text("Actions taken:")
    return NOTE_ACTIONS


async def note_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Please describe actions taken.")
        return NOTE_ACTIONS
    context.user_data["support_note_actions"] = text
    await update.message.reply_text("Next steps:")
    return NOTE_NEXT_STEPS


async def note_next_steps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Please describe next steps.")
        return NOTE_NEXT_STEPS

    club_id = context.user_data.get("support_note_club_id")
    gg_player_id = context.user_data.get("support_note_gg_player_id")
    admin_id = context.user_data.get("support_note_admin_id")
    if club_id is None or not gg_player_id or admin_id is None:
        await update.message.reply_text("Note setup expired. Send /note to start again.")
        _cleanup_note_flow(context)
        return ConversationHandler.END

    try:
        note, issue = add_note(
            club_id=int(club_id),
            gg_player_id=str(gg_player_id),
            situation=str(context.user_data.get("support_note_situation") or ""),
            actions_taken=str(context.user_data.get("support_note_actions") or ""),
            next_steps=text,
            created_by_telegram_user_id=int(admin_id),
            source_telegram_chat_id=context.user_data.get("support_note_source_chat_id"),
            telegram_chat_id=context.user_data.get("support_note_source_chat_id"),
        )
    except SupportNoteValidationError as exc:
        await update.message.reply_text(str(exc))
        return NOTE_NEXT_STEPS

    with get_db() as session:
        label = club_label_for_id(session, int(club_id))

    await update.message.reply_text(
        format_note_saved(
            note_id=note.id,
            club_label=label,
            gg_player_id=issue.gg_player_id,
            status=issue.status,
        )
    )
    _cleanup_note_flow(context)
    return ConversationHandler.END


async def support_note_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("Support note cancelled.")
    _cleanup_note_flow(context)
    return ConversationHandler.END


async def support_note_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        try:
            await update.message.reply_text("Support note timed out.")
        except Exception:
            pass
    _cleanup_note_flow(context)
    return ConversationHandler.END


async def notes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not update.effective_chat:
        return

    if update.effective_chat.type != ChatType.PRIVATE:
        await update.message.reply_text("Use /notes in a private chat with this bot.")
        return

    user_id = update.effective_user.id
    if not _can_use_support_notes(user_id):
        await update.message.reply_text("You are not allowed to use support notes.")
        return

    args = context.args or []
    if args:
        try:
            gg_player_id = validate_gg_player_id(args[0])
        except SupportNoteValidationError as exc:
            await update.message.reply_text(str(exc))
            return
        text = format_player_note_history(get_player_note_history(gg_player_id))
    else:
        text = format_open_issues_list(list_open_issues())

    await _reply_long(update.message, text)


async def resolve_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not update.effective_chat:
        return

    if update.effective_chat.type != ChatType.PRIVATE:
        await update.message.reply_text("Use /resolve in a private chat with this bot.")
        return

    user_id = update.effective_user.id
    if not _can_use_support_notes(user_id):
        await update.message.reply_text("You are not allowed to use support notes.")
        return

    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /resolve PLAYER_ID\nExample: /resolve 8190-5287")
        return

    try:
        gg_player_id = validate_gg_player_id(args[0])
    except SupportNoteValidationError as exc:
        await update.message.reply_text(str(exc))
        return

    result = resolve_issues_for_player(
        gg_player_id,
        resolved_by_telegram_user_id=user_id,
    )
    await update.message.reply_text(format_resolve_result(result))


_NOTE_CANCEL = CommandHandler("cancel", support_note_cancel)


def get_note_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("note", note_entry)],
        states={
            NOTE_PLAYER_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, note_player_id),
                _NOTE_CANCEL,
            ],
            NOTE_CLUB: [
                CallbackQueryHandler(note_club_chosen, pattern=r"^snclub:\d+$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, note_club_text),
                _NOTE_CANCEL,
            ],
            NOTE_SITUATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, note_situation),
                _NOTE_CANCEL,
            ],
            NOTE_ACTIONS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, note_actions),
                _NOTE_CANCEL,
            ],
            NOTE_NEXT_STEPS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, note_next_steps),
                _NOTE_CANCEL,
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, support_note_timeout),
            ],
        },
        fallbacks=[_NOTE_CANCEL],
        conversation_timeout=600,
        name="support_note_conv",
        per_chat=False,
        per_user=True,
    )
