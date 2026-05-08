"""Admin /bonus conversation: record a bonus with player, amount, type, and club."""

import os
from decimal import Decimal, InvalidOperation

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from config import ADMIN_USER_IDS
from db.connection import get_db
from db.models import BonusType, BonusRecord, Club

(
    BONUS_USERNAME,
    BONUS_AMOUNT,
    BONUS_TYPE,
    BONUS_DESCRIPTION,
    BONUS_CLUB,
) = range(5)

ZAPIER_WEBHOOK_ENV = "ZAPIER_BONUS_WEBHOOK_URL"


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


async def bonus_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return ConversationHandler.END

    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        return ConversationHandler.END

    context.user_data["bonus_admin_id"] = user_id
    await update.message.reply_text("Player Username:")
    return BONUS_USERNAME


async def bonus_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END
    username = (update.message.text or "").strip()
    if not username:
        await update.message.reply_text("Please enter a valid username.")
        return BONUS_USERNAME
    context.user_data["bonus_player"] = username
    await update.message.reply_text("Amount ($):")
    return BONUS_AMOUNT


async def bonus_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END
    raw = (update.message.text or "").strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(raw)
        if amount <= 0:
            raise InvalidOperation()
    except (InvalidOperation, Exception):
        await update.message.reply_text("Please enter a valid dollar amount (e.g. 50 or 100.00).")
        return BONUS_AMOUNT

    context.user_data["bonus_amount"] = amount

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

    await update.message.reply_text(
        "Select Bonus Type:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return BONUS_TYPE


async def bonus_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if not data.startswith("btype:"):
        return ConversationHandler.END

    choice = data.split(":", 1)[1]
    if choice == "other":
        context.user_data["bonus_type_id"] = None
        context.user_data["bonus_type_name"] = "Other"
        await query.edit_message_text("You selected: Other\n\nPlease enter a description for this bonus:")
        return BONUS_DESCRIPTION

    type_id = int(choice)
    types = _get_bonus_types()
    type_name = next((t["name"] for t in types if t["id"] == type_id), "Unknown")
    context.user_data["bonus_type_id"] = type_id
    context.user_data["bonus_type_name"] = type_name
    await query.edit_message_text(f"Bonus Type: {type_name}")
    return await _ask_club(query.message.chat, context)


async def bonus_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END
    desc = (update.message.text or "").strip()
    if not desc:
        await update.message.reply_text("Please enter a description.")
        return BONUS_DESCRIPTION
    context.user_data["bonus_custom_desc"] = desc
    context.user_data["bonus_type_name"] = f"Other — {desc}"
    return await _ask_club(update.message.chat, context)


async def _ask_club(chat, context):
    clubs = _get_clubs()
    if not clubs:
        await chat.send_message("No active clubs found. Please add a club first.")
        _cleanup(context)
        return ConversationHandler.END

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
    return BONUS_CLUB


async def bonus_club_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if not data.startswith("bclub:"):
        return ConversationHandler.END

    club_id = int(data.split(":")[1])
    clubs = _get_clubs()
    club_name = next((c["name"] for c in clubs if c["id"] == club_id), "Unknown")
    context.user_data["bonus_club_id"] = club_id
    context.user_data["bonus_club_name"] = club_name

    player = context.user_data.get("bonus_player", "?")
    amount = context.user_data.get("bonus_amount", "?")
    type_name = context.user_data.get("bonus_type_name", "?")
    desc = context.user_data.get("bonus_custom_desc")

    record_data = {
        "player_username": player,
        "amount": amount,
        "bonus_type_id": context.user_data.get("bonus_type_id"),
        "custom_description": desc,
        "club_id": club_id,
        "club_name": club_name,
        "bonus_type_name": type_name,
        "admin_user_id": context.user_data.get("bonus_admin_id"),
    }

    _save_record(record_data)
    await _fire_zapier_webhook(record_data)

    summary = (
        f"Bonus recorded!\n\n"
        f"Player: {player}\n"
        f"Amount: ${amount}\n"
        f"Type: {type_name}\n"
        f"Club: {club_name}"
    )
    await query.edit_message_text(summary)
    _cleanup(context)
    return ConversationHandler.END


def _cleanup(context):
    for key in (
        "bonus_admin_id",
        "bonus_player",
        "bonus_amount",
        "bonus_type_id",
        "bonus_type_name",
        "bonus_custom_desc",
        "bonus_club_id",
        "bonus_club_name",
    ):
        context.user_data.pop(key, None)


async def bonus_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("Bonus recording cancelled.")
    _cleanup(context)
    return ConversationHandler.END


async def bonus_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        try:
            await update.message.reply_text("Bonus recording timed out.")
        except Exception:
            pass
    _cleanup(context)


def get_bonus_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("bonus", bonus_entry)],
        states={
            BONUS_USERNAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bonus_username),
            ],
            BONUS_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bonus_amount),
            ],
            BONUS_TYPE: [
                CallbackQueryHandler(bonus_type_chosen, pattern=r"^btype:"),
            ],
            BONUS_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bonus_description),
            ],
            BONUS_CLUB: [
                CallbackQueryHandler(bonus_club_chosen, pattern=r"^bclub:\d+$"),
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, bonus_timeout),
            ],
        },
        fallbacks=[CommandHandler("cancel", bonus_cancel)],
        conversation_timeout=300,
        name="bonus_conv",
        per_chat=False,
        per_user=True,
    )
