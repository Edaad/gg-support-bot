"""Deposit conversation: amount first, then filtered method selection, then optional crypto sub-option."""

from decimal import Decimal, InvalidOperation

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
from bot.services.club import (
    get_club_for_chat,
    get_methods_for_amount,
    get_method_by_id,
    get_sub_options,
    get_sub_option_by_id,
    get_club_allows_admin_commands,
    get_club_simple_mode,
    get_tier_for_amount,
    get_lowest_minimum,
    record_activity,
)
from bot.handlers.response_utils import send_response_messages

DEPOSIT_AMOUNT, DEPOSIT_CHOOSE, DEPOSIT_SUB = range(3)


async def deposit_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return ConversationHandler.END
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use /deposit in a club group.")
        return ConversationHandler.END
    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        await update.message.reply_text(
            "This group isn't linked to a club yet. The club owner must add the bot."
        )
        return ConversationHandler.END

    user_id = update.effective_user.id
    if user_id in ADMIN_USER_IDS and not get_club_allows_admin_commands(club_id):
        return ConversationHandler.END

    simple = get_club_simple_mode(club_id, "deposit")
    if simple:
        await _send_simple_response(update.message, simple)
        try:
            record_activity(club_id, user_id, chat.id, "deposit")
        except Exception:
            pass
        return ConversationHandler.END

    context.user_data["deposit_club_id"] = club_id
    context.user_data["deposit_chat_id"] = chat.id
    context.user_data["deposit_user_id"] = user_id
    await update.message.reply_text("How much would you like to deposit?")
    return DEPOSIT_AMOUNT


async def deposit_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return ConversationHandler.END
    club_id = context.user_data.get("deposit_club_id")
    if not club_id:
        return ConversationHandler.END

    raw = (update.message.text or "").strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(raw)
        if amount <= 0:
            raise InvalidOperation()
    except (InvalidOperation, Exception):
        await update.message.reply_text(
            "Please enter a valid dollar amount (Example: 50 or 100.00)."
        )
        return DEPOSIT_AMOUNT

    context.user_data["deposit_amount"] = amount
    methods = get_methods_for_amount(club_id, "deposit", amount)
    if not methods:
        lowest = get_lowest_minimum(club_id, "deposit")
        if lowest is not None and amount < lowest:
            await update.message.reply_text(
                f"Sorry! The minimum deposit amount is ${lowest:,.2f}."
            )
        else:
            await update.message.reply_text(
                f"No deposit methods available for ${amount}. Please try a different amount."
            )
        return ConversationHandler.END

    buttons = []
    row = []
    for m in methods:
        row.append(InlineKeyboardButton(m["name"], callback_data=f"dep:{m['id']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    await update.message.reply_text(
        f"Deposit amount: ${amount}\nSelect your deposit method:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return DEPOSIT_CHOOSE


async def deposit_method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if not data.startswith("dep:"):
        return ConversationHandler.END

    method_id = int(data.split(":")[1])
    method = get_method_by_id(method_id)
    if not method:
        await query.edit_message_text("That method is no longer available.")
        return ConversationHandler.END

    context.user_data["deposit_method_name"] = method["name"]

    if method["has_sub_options"]:
        subs = get_sub_options(method_id)
        if subs:
            buttons = []
            row = []
            for s in subs:
                row.append(
                    InlineKeyboardButton(s["name"], callback_data=f"depsub:{s['id']}")
                )
                if len(row) == 2:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            await query.edit_message_text(
                f"You selected {method['name']}. Which option?",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return DEPOSIT_SUB

    amount = context.user_data.get("deposit_amount", "?")
    tier = get_tier_for_amount(method_id, amount) if isinstance(amount, Decimal) else None
    await _send_response(query, tier or method, amount, method["name"])
    _record_deposit(context)
    _cleanup(context)
    return ConversationHandler.END


async def deposit_sub_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if not data.startswith("depsub:"):
        return ConversationHandler.END

    sub_id = int(data.split(":")[1])
    sub = get_sub_option_by_id(sub_id)
    if not sub:
        await query.edit_message_text("That option is no longer available.")
        _cleanup(context)
        return ConversationHandler.END

    amount = context.user_data.get("deposit_amount", "?")
    method_name = context.user_data.get("deposit_method_name", "")
    display = f"{method_name} — {sub['name']}"

    await _send_response(query, sub, amount, display)
    _record_deposit(context)
    _cleanup(context)
    return ConversationHandler.END


def _record_deposit(context):
    club_id = context.user_data.get("deposit_club_id")
    user_id = context.user_data.get("deposit_user_id")
    chat_id = context.user_data.get("deposit_chat_id")
    if club_id and user_id and chat_id:
        try:
            record_activity(club_id, user_id, chat_id, "deposit")
        except Exception:
            pass


async def _send_response(query, data, amount, display_name):
    """Edit the keyboard message to the announcement, then send instructions as a new message below."""
    announcement = f"Deposit request for ${amount} via {display_name}"
    await query.edit_message_text(announcement)
    await send_response_messages(query.message.chat, data)


async def _send_simple_response(message, data):
    """Send the simple-mode response (text or photo) directly."""
    await send_response_messages(message, data)


def _cleanup(context):
    for key in (
        "deposit_club_id",
        "deposit_chat_id",
        "deposit_user_id",
        "deposit_amount",
        "deposit_method_name",
    ):
        context.user_data.pop(key, None)


async def deposit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("Deposit cancelled.")
    _cleanup(context)
    return ConversationHandler.END


def get_deposit_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("deposit", deposit_entry)],
        states={
            DEPOSIT_AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, deposit_amount_received
                ),
            ],
            DEPOSIT_CHOOSE: [
                CallbackQueryHandler(deposit_method_chosen, pattern=r"^dep:\d+$"),
            ],
            DEPOSIT_SUB: [
                CallbackQueryHandler(deposit_sub_chosen, pattern=r"^depsub:\d+$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", deposit_cancel)],
        name="deposit_conv",
        per_chat=True,
        per_user=True,
    )
