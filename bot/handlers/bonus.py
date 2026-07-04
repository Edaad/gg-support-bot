"""Admin /bonus flow: record a bonus with player, amount, type, and club."""

from __future__ import annotations

import logging
import os
from decimal import Decimal, InvalidOperation

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config import ADMIN_USER_IDS
from bot.handlers.flow_cancel import ACTIVE_FLOW_KEY, clear_active_flow, mark_active_flow
from bot.services.bonus_drafts import cancel_draft, draft_to_context, get_pending_draft, mark_draft_submitted
from db.connection import get_db
from db.models import BonusType, BonusRecord, Club

logger = logging.getLogger(__name__)

BONUS_STEP_KEY = "bonus_step"
BONUS_TIMEOUT_SECONDS = 300
ZAPIER_WEBHOOK_ENV = "ZAPIER_BONUS_WEBHOOK_URL"

_TEXT_STEPS = frozenset({"username", "amount", "description"})


def _get_bonus_types():
    with get_db() as session:
        rows = (
            session.query(BonusType)
            .filter(BonusType.is_active.is_(True))
            .order_by(BonusType.sort_order, BonusType.id)
            .all()
        )
        return [{"id": r.id, "name": r.name} for r in rows]


def _get_clubs():
    with get_db() as session:
        rows = (
            session.query(Club)
            .filter(Club.is_active.is_(True))
            .order_by(Club.name)
            .all()
        )
        return [{"id": r.id, "name": r.name} for r in rows]


def _club_name_for_id(club_id: int) -> str:
    return next((c["name"] for c in _get_clubs() if c["id"] == club_id), "Unknown")


def _type_keyboard_markup() -> InlineKeyboardMarkup:
    types = _get_bonus_types()
    buttons = []
    row = []
    for t in types:
        row.append(InlineKeyboardButton(t["name"], callback_data=f"btype:{t['id']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("Other", callback_data="btype:other")])
    return InlineKeyboardMarkup(buttons)


def _save_record(data: dict) -> int:
    with get_db() as session:
        rec = BonusRecord(
            player_username=data["player_username"],
            amount=data["amount"],
            bonus_type_id=data.get("bonus_type_id"),
            custom_description=data.get("custom_description"),
            club_id=data["club_id"],
            admin_telegram_user_id=data["admin_user_id"],
        )
        session.add(rec)
        session.flush()
        return rec.id


async def _fire_zapier_webhook(data: dict):
    url = os.getenv(ZAPIER_WEBHOOK_ENV)
    if not url:
        return
    payload = {
        "player_username": data["player_username"],
        "amount": str(data["amount"]),
        "bonus_type": data.get("bonus_type_name", ""),
        "description": data.get("custom_description", ""),
        "club": data.get("club_name", ""),
        "admin_telegram_user_id": data["admin_user_id"],
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except Exception:
        pass


def bonus_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.user_data.get(BONUS_STEP_KEY))


def _timeout_job_name(user_id: int) -> str:
    return f"bonus_timeout_{user_id}"


def _cancel_bonus_timeout(context: ContextTypes.DEFAULT_TYPE, user_id: int | None) -> None:
    if not user_id or not context.job_queue:
        return
    try:
        for job in context.job_queue.get_jobs_by_name(_timeout_job_name(user_id)):
            job.schedule_removal()
    except Exception:
        pass


def _schedule_bonus_timeout(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    user_id: int,
) -> None:
    if not context.job_queue:
        return
    _cancel_bonus_timeout(context, user_id)
    try:
        context.job_queue.run_once(
            _bonus_timeout_callback,
            when=BONUS_TIMEOUT_SECONDS,
            chat_id=chat_id,
            user_id=user_id,
            name=_timeout_job_name(user_id),
        )
    except Exception:
        logger.warning("Failed to schedule bonus timeout user_id=%s", user_id, exc_info=True)


async def _bonus_timeout_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not bonus_flow_active(context):
        return
    chat_id = context.job.chat_id if context.job else None
    if not chat_id:
        return
    try:
        await context.bot.send_message(chat_id=chat_id, text="Bonus recording timed out.")
    except Exception:
        pass
    _cleanup(context)


def _cleanup(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.user_data.get("bonus_admin_id")
    _cancel_bonus_timeout(context, int(user_id) if user_id else None)
    clear_active_flow(context)
    context.user_data.pop(BONUS_STEP_KEY, None)
    for key in (
        "bonus_admin_id",
        "bonus_draft_id",
        "bonus_player",
        "bonus_amount",
        "bonus_type_id",
        "bonus_type_name",
        "bonus_custom_desc",
        "bonus_club_id",
        "bonus_club_name",
    ):
        context.user_data.pop(key, None)


def _is_bonus_admin(update: Update) -> bool:
    return bool(
        update.effective_user
        and update.effective_user.id in ADMIN_USER_IDS
    )


def _is_bonus_actor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.effective_user:
        return False
    admin_id = context.user_data.get("bonus_admin_id")
    return admin_id is not None and update.effective_user.id == admin_id


async def _finalize_bonus_record(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    query=None,
    chat=None,
) -> None:
    club_id = context.user_data.get("bonus_club_id")
    club_name = context.user_data.get("bonus_club_name") or (
        _club_name_for_id(int(club_id)) if club_id else "Unknown"
    )
    player = context.user_data.get("bonus_player", "?")
    amount = context.user_data.get("bonus_amount", "?")
    type_name = context.user_data.get("bonus_type_name", "?")

    record_data = {
        "player_username": player,
        "amount": amount,
        "bonus_type_id": context.user_data.get("bonus_type_id"),
        "custom_description": context.user_data.get("bonus_custom_desc"),
        "club_id": club_id,
        "club_name": club_name,
        "bonus_type_name": type_name,
        "admin_user_id": context.user_data.get("bonus_admin_id"),
    }

    _save_record(record_data)
    await _fire_zapier_webhook(record_data)

    draft_id = context.user_data.get("bonus_draft_id")
    if draft_id:
        with get_db() as session:
            mark_draft_submitted(session, int(draft_id))

    summary = (
        f"Bonus recorded!\n\n"
        f"Player: {player}\n"
        f"Amount: ${amount}\n"
        f"Type: {type_name}\n"
        f"Club: {club_name}"
    )
    if query:
        await query.edit_message_text(summary)
    elif chat:
        await chat.send_message(summary)
    _cleanup(context)


async def bonus_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not update.effective_chat:
        return

    if update.effective_chat.type != ChatType.PRIVATE:
        await update.message.reply_text("Use /bonus in a private chat with this bot.")
        return

    if not _is_bonus_admin(update):
        return

    from bot.handlers.inactive_outreach_send import _cleanup_send_flow

    _cleanup_send_flow(context)
    _cleanup(context)
    user_id = update.effective_user.id
    context.user_data["bonus_admin_id"] = user_id
    context.user_data[BONUS_STEP_KEY] = "username"
    mark_active_flow(context, "bonus")
    _schedule_bonus_timeout(
        context,
        chat_id=update.effective_chat.id,
        user_id=user_id,
    )
    await update.message.reply_text("Player Username:")
    raise ApplicationHandlerStop()


async def bonus_draft_continue_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return

    await query.answer()

    try:
        draft_id = int(query.data.split(":", 1)[1])
    except ValueError:
        await query.edit_message_text("Invalid draft. Send /bonus to start again.")
        raise ApplicationHandlerStop()

    try:
        with get_db() as session:
            draft = get_pending_draft(
                session,
                draft_id,
                staff_telegram_user_id=update.effective_user.id,
            )
            draft_ctx = draft_to_context(draft) if draft else None
    except Exception:
        logger.exception(
            "bonus: draft lookup failed draft_id=%s user_id=%s",
            draft_id,
            update.effective_user.id,
        )
        await query.edit_message_text(
            "Could not load bonus draft. Send /bonus to start again."
        )
        raise ApplicationHandlerStop()

    if not draft_ctx:
        await query.edit_message_text(
            "Bonus draft expired. Send /bonus to start again."
        )
        raise ApplicationHandlerStop()

    from bot.handlers.inactive_outreach_send import _cleanup_send_flow

    _cleanup_send_flow(context)
    _cleanup(context)

    user_id = update.effective_user.id
    context.user_data["bonus_admin_id"] = user_id
    context.user_data["bonus_draft_id"] = draft_ctx.id
    context.user_data["bonus_amount"] = draft_ctx.amount
    if draft_ctx.player_username:
        context.user_data["bonus_player"] = draft_ctx.player_username
    if draft_ctx.club_id is not None:
        context.user_data["bonus_club_id"] = draft_ctx.club_id
        context.user_data["bonus_club_name"] = _club_name_for_id(draft_ctx.club_id)

    mark_active_flow(context, "bonus")
    _schedule_bonus_timeout(
        context,
        chat_id=user_id,
        user_id=user_id,
    )

    if draft_ctx.player_username and draft_ctx.amount is not None:
        context.user_data[BONUS_STEP_KEY] = "type"
        await query.edit_message_text(
            "Select Bonus Type:",
            reply_markup=_type_keyboard_markup(),
        )
    else:
        context.user_data[BONUS_STEP_KEY] = "username"
        await query.edit_message_text("Player Username:")

    raise ApplicationHandlerStop()


async def bonus_draft_cancel_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()
    try:
        draft_id = int(query.data.split(":", 1)[1])
    except ValueError:
        await query.edit_message_text("Cancelled.")
        raise ApplicationHandlerStop()

    with get_db() as session:
        cancel_draft(session, draft_id)
    await query.edit_message_text("Bonus recording cancelled.")
    raise ApplicationHandlerStop()


async def bonus_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """High-priority private text handler while /bonus is in progress."""
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    if update.effective_chat.type != ChatType.PRIVATE:
        return

    if context.user_data.get(ACTIVE_FLOW_KEY) == "inactive_outreach_send":
        return

    step = context.user_data.get(BONUS_STEP_KEY)
    if step not in _TEXT_STEPS:
        return
    if not _is_bonus_actor(update, context):
        return

    logger.info("bonus step=%s user_id=%s text=%r", step, update.effective_user.id, (update.message.text or "")[:40])

    if step == "username":
        await _handle_username(update, context)
    elif step == "amount":
        await _handle_amount(update, context)
    elif step == "description":
        await _handle_description(update, context)

    raise ApplicationHandlerStop()


async def bonus_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """High-priority callback handler for bonus type/club buttons."""
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return

    step = context.user_data.get(BONUS_STEP_KEY)
    data = query.data
    if step == "type" and data.startswith("btype:"):
        if not _is_bonus_actor(update, context):
            return
        await _handle_type_chosen(update, context)
        raise ApplicationHandlerStop()
    if step == "club" and data.startswith("bclub:"):
        if not _is_bonus_actor(update, context):
            return
        await _handle_club_chosen(update, context)
        raise ApplicationHandlerStop()


async def _handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    username = (update.message.text or "").strip()
    if not username:
        await update.message.reply_text("Please enter a valid username.")
        return
    context.user_data["bonus_player"] = username
    if context.user_data.get("bonus_amount") is not None:
        context.user_data[BONUS_STEP_KEY] = "type"
        await update.message.reply_text(
            "Select Bonus Type:",
            reply_markup=_type_keyboard_markup(),
        )
    else:
        context.user_data[BONUS_STEP_KEY] = "amount"
        await update.message.reply_text("Amount ($):")


async def _handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    raw = (update.message.text or "").strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(raw)
        if amount <= 0:
            raise InvalidOperation()
    except (InvalidOperation, Exception):
        await update.message.reply_text("Please enter a valid dollar amount (e.g. 50 or 100.00).")
        return

    context.user_data["bonus_amount"] = amount
    context.user_data[BONUS_STEP_KEY] = "type"

    await update.message.reply_text(
        "Select Bonus Type:",
        reply_markup=_type_keyboard_markup(),
    )


async def _handle_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query and query.data

    await query.answer()
    choice = query.data.split(":", 1)[1]
    if choice == "other":
        context.user_data["bonus_type_id"] = None
        context.user_data["bonus_type_name"] = "Other"
        context.user_data[BONUS_STEP_KEY] = "description"
        await query.edit_message_text(
            "You selected: Other\n\nPlease enter a description for this bonus:"
        )
        return

    type_id = int(choice)
    types = _get_bonus_types()
    type_name = next((t["name"] for t in types if t["id"] == type_id), "Unknown")
    context.user_data["bonus_type_id"] = type_id
    context.user_data["bonus_type_name"] = type_name
    await query.edit_message_text(f"Bonus Type: {type_name}")
    if context.user_data.get("bonus_club_id"):
        await _finalize_bonus_record(context, query=query)
    else:
        await _ask_club(query.message.chat, context)


async def _handle_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    desc = (update.message.text or "").strip()
    if not desc:
        await update.message.reply_text("Please enter a description.")
        return
    context.user_data["bonus_custom_desc"] = desc
    context.user_data["bonus_type_name"] = f"Other — {desc}"
    if context.user_data.get("bonus_club_id"):
        await _finalize_bonus_record(context, chat=update.message.chat)
    else:
        await _ask_club(update.message.chat, context)


async def _ask_club(chat, context: ContextTypes.DEFAULT_TYPE) -> None:
    clubs = _get_clubs()
    if not clubs:
        await chat.send_message("No active clubs found. Please add a club first.")
        _cleanup(context)
        return

    context.user_data[BONUS_STEP_KEY] = "club"
    buttons = []
    row = []
    for c in clubs:
        row.append(InlineKeyboardButton(c["name"], callback_data=f"bclub:{c['id']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    await chat.send_message(
        "Select Club:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _handle_club_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query and query.data

    await query.answer()
    club_id = int(query.data.split(":")[1])
    club_name = _club_name_for_id(club_id)
    context.user_data["bonus_club_id"] = club_id
    context.user_data["bonus_club_name"] = club_name
    await _finalize_bonus_record(context, query=query)


async def bonus_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    draft_id = context.user_data.get("bonus_draft_id")
    if draft_id:
        with get_db() as session:
            cancel_draft(session, int(draft_id))
    if update.message:
        await update.message.reply_text("Bonus recording cancelled.")
    _cleanup(context)
