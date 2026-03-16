"""Cashout conversation: amount first, filtered methods, optional multi-method with inline Done button."""

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
    get_club_allows_multi_cashout,
    get_club_allows_admin_commands,
    get_tier_for_amount,
)

CASHOUT_AMOUNT, CASHOUT_CHOOSE, CASHOUT_SUB = range(3)


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

    user_id = update.effective_user.id
    if user_id in ADMIN_USER_IDS and not get_club_allows_admin_commands(club_id):
        return ConversationHandler.END

    context.user_data["cashout_club_id"] = club_id
    context.user_data["cashout_chat_id"] = chat.id
    context.user_data["cashout_selected"] = []
    context.user_data["cashout_multi"] = get_club_allows_multi_cashout(club_id)
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
        await update.message.reply_text(
            "Please enter a valid dollar amount (Example: 50 or 100.00)."
        )
        return CASHOUT_AMOUNT

    context.user_data["cashout_amount"] = amount
    return await _show_method_keyboard(update, context, first_pick=True)


async def _show_method_keyboard(update, context, first_pick=False):
    """Show available methods. After the first pick (multi-mode), includes a Done button."""
    club_id = context.user_data["cashout_club_id"]
    amount = context.user_data["cashout_amount"]
    already_selected = {s["id"] for s in context.user_data.get("cashout_selected", [])}
    is_multi = context.user_data.get("cashout_multi", False)

    methods = get_methods_for_amount(club_id, "cashout", amount)
    available = [m for m in methods if m["id"] not in already_selected]

    if not available:
        return await _finalize_cashout(update, context)

    buttons = []
    row = []
    for m in available:
        row.append(InlineKeyboardButton(m["name"], callback_data=f"co:{m['id']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    if is_multi and not first_pick:
        buttons.append(
            [InlineKeyboardButton("Done — Submit cashout", callback_data="codone")]
        )

    if first_pick:
        text = f"Cashout amount: ${amount}\nSelect your cashout method:"
    else:
        text = "Select another cashout method, or tap Done to submit:"

    if first_pick and update.message:
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(buttons)
        )
    elif update.callback_query:
        await update.callback_query.message.chat.send_message(
            text, reply_markup=InlineKeyboardMarkup(buttons)
        )
    return CASHOUT_CHOOSE


async def cashout_method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()

    data = query.data or ""

    if data == "codone":
        await query.edit_message_text("Submitting cashout...")
        return await _finalize_cashout(update, context)

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
                row.append(
                    InlineKeyboardButton(s["name"], callback_data=f"cosub:{s['id']}")
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
            return CASHOUT_SUB

    amount = context.user_data.get("cashout_amount", "?")
    tier = get_tier_for_amount(method_id, amount) if isinstance(amount, Decimal) else None
    await _send_response(query, tier or method, amount, method["name"])
    context.user_data.setdefault("cashout_selected", []).append(
        {"id": method_id, "name": method["name"]}
    )

    is_multi = context.user_data.get("cashout_multi", False)
    if not is_multi:
        return await _finalize_cashout(update, context)

    return await _show_method_keyboard(update, context, first_pick=False)


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
    await _send_response(query, sub, amount, display)

    context.user_data.setdefault("cashout_selected", []).append(
        {"id": method.get("id"), "name": display}
    )

    is_multi = context.user_data.get("cashout_multi", False)
    if not is_multi:
        return await _finalize_cashout(update, context)

    return await _show_method_keyboard(update, context, first_pick=False)


async def _send_response(query, data, amount, display_name):
    """Edit the keyboard message to the announcement, then send instructions as a new message."""
    announcement = f"Cashout request for ${amount} via {display_name}"
    await query.edit_message_text(announcement)

    if data["response_type"] == "photo" and data.get("response_file_id"):
        await query.message.chat.send_photo(
            photo=data["response_file_id"],
            caption=data.get("response_caption") or None,
        )
    else:
        text = data.get("response_text") or ""
        if text:
            await query.message.chat.send_message(text)


async def _finalize_cashout(update, context):
    amount = context.user_data.get("cashout_amount", "?")
    selected = context.user_data.get("cashout_selected", [])
    method_names = ", ".join(s["name"] for s in selected) if selected else "None"

    summary = f"Cashout submitted: ${amount} via {method_names}"
    try:
        if update.callback_query:
            await update.callback_query.message.chat.send_message(summary)
        elif update.message:
            await update.message.chat.send_message(summary)
    except Exception:
        pass

    _cleanup(context)
    return ConversationHandler.END


def _cleanup(context):
    for key in (
        "cashout_club_id",
        "cashout_chat_id",
        "cashout_amount",
        "cashout_selected",
        "cashout_current_method",
        "cashout_multi",
    ):
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
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, cashout_amount_received
                ),
            ],
            CASHOUT_CHOOSE: [
                CallbackQueryHandler(cashout_method_chosen, pattern=r"^co:\d+$"),
                CallbackQueryHandler(cashout_method_chosen, pattern=r"^codone$"),
            ],
            CASHOUT_SUB: [
                CallbackQueryHandler(cashout_sub_chosen, pattern=r"^cosub:\d+$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cashout_cancel)],
        name="cashout_conv",
        per_chat=True,
        per_user=True,
    )
