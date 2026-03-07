"""Cashout conversation: amount first, filtered methods, multi-method selection, crypto sub-options."""

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

from bot.services.club import get_club_for_chat, get_methods_for_amount, get_method_by_id, get_sub_options, get_sub_option_by_id

CASHOUT_AMOUNT, CASHOUT_CHOOSE, CASHOUT_SUB, CASHOUT_MORE = range(4)


async def cashout_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return ConversationHandler.END
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use /cashout in a club group.")
        return ConversationHandler.END
    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        await update.message.reply_text(
            "This group isn't linked to a club yet. The club owner must add the bot."
        )
        return ConversationHandler.END
    context.user_data["cashout_club_id"] = club_id
    context.user_data["cashout_chat_id"] = chat.id
    context.user_data["cashout_selected"] = []
    await update.message.reply_text("How much would you like to cashout?")
    return CASHOUT_AMOUNT


async def cashout_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END
    club_id = context.user_data.get("cashout_club_id")
    if not club_id:
        return ConversationHandler.END

    raw = (update.message.text or "").strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(raw)
        if amount <= 0:
            raise InvalidOperation()
    except (InvalidOperation, Exception):
        await update.message.reply_text("Please enter a valid dollar amount (e.g. 50 or 100.00).")
        return CASHOUT_AMOUNT

    context.user_data["cashout_amount"] = amount
    return await _show_method_keyboard(update, context, amount, is_callback=False)


async def _show_method_keyboard(update, context, amount, is_callback=False):
    club_id = context.user_data["cashout_club_id"]
    already_selected = {s["id"] for s in context.user_data.get("cashout_selected", [])}

    methods = get_methods_for_amount(club_id, "cashout", amount)
    available = [m for m in methods if m["id"] not in already_selected]

    if not available:
        if context.user_data.get("cashout_selected"):
            return await _finalize_cashout(update, context, is_callback)
        msg = f"No cashout methods available for ${amount}. Please try a different amount."
        if is_callback:
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
        _cleanup(context)
        return ConversationHandler.END

    buttons = []
    row = []
    for m in available:
        row.append(InlineKeyboardButton(m["name"], callback_data=f"co:{m['id']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    text = f"Cashout amount: ${amount}\nSelect your cashout method:"
    if is_callback:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    return CASHOUT_CHOOSE


async def cashout_method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if not data.startswith("co:"):
        return ConversationHandler.END

    method_id = int(data.split(":")[1])
    method = get_method_by_id(method_id)
    if not method:
        await query.edit_message_text("That method is no longer available.")
        return ConversationHandler.END

    context.user_data["cashout_current_method"] = method

    if method["has_sub_options"]:
        subs = get_sub_options(method_id)
        if subs:
            buttons = []
            row = []
            for s in subs:
                row.append(InlineKeyboardButton(s["name"], callback_data=f"cosub:{s['id']}"))
                if len(row) == 2:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            await query.edit_message_text(
                f"You selected {method['name']}. Which option?",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return CASHOUT_SUB

    amount = context.user_data.get("cashout_amount", "?")
    await _send_method_response(query, method, amount)
    context.user_data.setdefault("cashout_selected", []).append({"id": method_id, "name": method["name"]})
    return await _ask_more(query, context)


async def cashout_sub_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if not data.startswith("cosub:"):
        return ConversationHandler.END

    sub_id = int(data.split(":")[1])
    sub = get_sub_option_by_id(sub_id)
    method = context.user_data.get("cashout_current_method", {})
    if not sub:
        await query.edit_message_text("That option is no longer available.")
        _cleanup(context)
        return ConversationHandler.END

    amount = context.user_data.get("cashout_amount", "?")
    display = f"{method.get('name', '')} — {sub['name']}"
    await _send_sub_response(query, sub, amount, display)

    context.user_data.setdefault("cashout_selected", []).append({"id": method.get("id"), "name": display})
    return await _ask_more(query, context)


async def _ask_more(query, context):
    buttons = [
        [
            InlineKeyboardButton("Yes", callback_data="comore:yes"),
            InlineKeyboardButton("No", callback_data="comore:no"),
        ]
    ]
    await query.message.reply_text(
        "Would you like to add another cashout method?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return CASHOUT_MORE


async def cashout_more_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()

    choice = (query.data or "").split(":")[1] if ":" in (query.data or "") else "no"

    if choice == "yes":
        amount = context.user_data.get("cashout_amount")
        return await _show_method_keyboard(update, context, amount, is_callback=True)

    return await _finalize_cashout(update, context, is_callback=True)


async def _finalize_cashout(update, context, is_callback=False):
    amount = context.user_data.get("cashout_amount", "?")
    selected = context.user_data.get("cashout_selected", [])
    method_names = ", ".join(s["name"] for s in selected) if selected else "None"

    summary = f"Cashout request submitted: ${amount} via {method_names}"
    try:
        chat = update.callback_query.message.chat if is_callback else update.message.chat
        await chat.send_message(summary)
    except Exception:
        pass

    if is_callback:
        await update.callback_query.edit_message_text(summary)
    else:
        await update.message.reply_text(summary)
    _cleanup(context)
    return ConversationHandler.END


async def _send_method_response(query, method, amount):
    try:
        await query.message.chat.send_message(
            f"Cashout request for ${amount} via {method['name']}"
        )
    except Exception:
        pass

    if method["response_type"] == "photo" and method.get("response_file_id"):
        await query.message.reply_photo(
            photo=method["response_file_id"],
            caption=method.get("response_caption") or None,
        )
    else:
        text = method.get("response_text") or ""
        if text:
            await query.edit_message_text(text)


async def _send_sub_response(query, sub, amount, display_name):
    try:
        await query.message.chat.send_message(
            f"Cashout request for ${amount} via {display_name}"
        )
    except Exception:
        pass

    if sub["response_type"] == "photo" and sub.get("response_file_id"):
        await query.message.reply_photo(
            photo=sub["response_file_id"],
            caption=sub.get("response_caption") or None,
        )
    else:
        text = sub.get("response_text") or ""
        if text:
            await query.edit_message_text(text)


def _cleanup(context):
    for key in ("cashout_club_id", "cashout_chat_id", "cashout_amount",
                "cashout_selected", "cashout_current_method"):
        context.user_data.pop(key, None)


async def cashout_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("Cashout cancelled.")
    _cleanup(context)
    return ConversationHandler.END


def get_cashout_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("cashout", cashout_entry)],
        states={
            CASHOUT_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cashout_amount_received),
            ],
            CASHOUT_CHOOSE: [
                CallbackQueryHandler(cashout_method_chosen, pattern=r"^co:\d+$"),
            ],
            CASHOUT_SUB: [
                CallbackQueryHandler(cashout_sub_chosen, pattern=r"^cosub:\d+$"),
            ],
            CASHOUT_MORE: [
                CallbackQueryHandler(cashout_more_chosen, pattern=r"^comore:"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cashout_cancel)],
        name="cashout_conv",
        per_chat=True,
        per_user=True,
    )
