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
    get_club_simple_mode,
    get_tier_for_amount,
    get_lowest_minimum,
    record_activity,
    cancel_last_cashout_activity,
    check_cashout_eligibility,
    is_club_staff,
    pick_variant,
    get_cashout_max_amount,
    get_cashout_soft_limit,
    update_group_name,
)
from bot.handlers.response_utils import send_response_messages

CASHOUT_AMOUNT, CASHOUT_CHOOSE, CASHOUT_SUB, CASHOUT_SIMPLE_AMOUNT = range(4)


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

    update_group_name(chat.id, chat.title)

    user_id = update.effective_user.id
    if user_id in ADMIN_USER_IDS and not get_club_allows_admin_commands(club_id):
        return ConversationHandler.END

    # Cooldown + business hours check (admins are exempt)
    is_admin = user_id in ADMIN_USER_IDS or is_club_staff(user_id, club_id)
    if not is_admin:
        eligible, deny_msg = check_cashout_eligibility(club_id, user_id)
        if not eligible:
            await update.message.reply_text(deny_msg)
            return ConversationHandler.END

    simple = get_club_simple_mode(club_id, "cashout")
    if simple:
        max_amt = get_cashout_max_amount(club_id)
        soft_limit = get_cashout_soft_limit(club_id)
        if max_amt is not None or soft_limit is not None:
            context.user_data["cashout_club_id"] = club_id
            context.user_data["cashout_chat_id"] = chat.id
            context.user_data["cashout_user_id"] = user_id
            context.user_data["cashout_simple_data"] = simple
            await update.message.reply_text("How much would you like to cashout?")
            return CASHOUT_SIMPLE_AMOUNT

        await _send_simple_response(update.message, simple)
        try:
            record_activity(club_id, user_id, chat.id, "cashout")
        except Exception:
            pass
        return ConversationHandler.END

    context.user_data["cashout_club_id"] = club_id
    context.user_data["cashout_chat_id"] = chat.id
    context.user_data["cashout_user_id"] = user_id
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

    max_amt = get_cashout_max_amount(club_id)
    if max_amt is not None and amount > max_amt:
        await update.message.reply_text(
            f"Please enter an amount below ${max_amt:,.2f} as that is our maximum "
            f"cashout amount per day! You can request another cashout for the "
            f"remaining amount after 24 hours."
        )
        return CASHOUT_AMOUNT

    context.user_data["cashout_amount"] = amount
    return await _show_method_keyboard(update, context, first_pick=True)


async def cashout_simple_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        return CASHOUT_SIMPLE_AMOUNT

    max_amt = get_cashout_max_amount(club_id)
    if max_amt is not None and amount > max_amt:
        await update.message.reply_text(
            f"Please enter an amount below ${max_amt:,.2f} as that is our maximum "
            f"cashout amount per day! You can request another cashout for the "
            f"remaining amount after 24 hours."
        )
        return CASHOUT_SIMPLE_AMOUNT

    simple_data = context.user_data.get("cashout_simple_data")
    user_id = context.user_data.get("cashout_user_id")
    chat_id = context.user_data.get("cashout_chat_id")
    if simple_data:
        await _send_simple_response(update.message, simple_data)

    soft = get_cashout_soft_limit(club_id)
    if soft is not None and amount > soft:
        try:
            await update.message.reply_text(
                f"${soft:,.2f} will be sent instantly, and your remaining "
                f"cashout will be sent within 24 hours!"
            )
        except Exception:
            pass

    try:
        record_activity(club_id, user_id, chat_id, "cashout")
    except Exception:
        pass
    _cleanup(context)
    return ConversationHandler.END


async def _show_method_keyboard(update, context, first_pick=False):
    """Show available methods. After the first pick (multi-mode), includes a Done button."""
    club_id = context.user_data["cashout_club_id"]
    amount = context.user_data["cashout_amount"]
    already_selected = {s["id"] for s in context.user_data.get("cashout_selected", [])}
    is_multi = context.user_data.get("cashout_multi", False)

    methods = get_methods_for_amount(club_id, "cashout", amount)
    available = [m for m in methods if m["id"] not in already_selected]

    if not available:
        if first_pick:
            lowest = get_lowest_minimum(club_id, "cashout")
            if lowest is not None and amount < lowest:
                msg = f"Sorry! The minimum cashout amount is ${lowest:,.2f}."
            else:
                msg = f"No cashout methods available for ${amount}. Please try a different amount."
            if update.message:
                await update.message.reply_text(msg)
            elif update.callback_query:
                await update.callback_query.message.chat.send_message(msg)
            _cleanup(context)
            return ConversationHandler.END
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
    if tier:
        response_data = pick_variant(method_id, tier_id=tier["id"]) or tier
    else:
        response_data = pick_variant(method_id) or method
    await _send_response(query, response_data, amount, method["name"])
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
    await send_response_messages(query.message.chat, data)


async def _finalize_cashout(update, context):
    amount = context.user_data.get("cashout_amount", "?")
    selected = context.user_data.get("cashout_selected", [])
    club_id = context.user_data.get("cashout_club_id")
    method_names = ", ".join(s["name"] for s in selected) if selected else "None"

    summary = f"Cashout submitted: ${amount} via {method_names}"
    chat = None
    try:
        if update.callback_query:
            chat = update.callback_query.message.chat
            await chat.send_message(summary)
        elif update.message:
            chat = update.message.chat
            await chat.send_message(summary)
    except Exception:
        pass

    if chat and club_id and isinstance(amount, Decimal):
        soft = get_cashout_soft_limit(club_id)
        if soft is not None and amount > soft:
            try:
                await chat.send_message(
                    f"${soft:,.2f} will be sent instantly, and your remaining "
                    f"cashout will be sent within 24 hours!"
                )
            except Exception:
                pass

    _record_cashout(context)
    _cleanup(context)
    return ConversationHandler.END


async def _send_simple_response(message, data):
    """Send the simple-mode response (text or photo) directly."""
    await send_response_messages(message, data)


def _record_cashout(context):
    club_id = context.user_data.get("cashout_club_id")
    user_id = context.user_data.get("cashout_user_id")
    chat_id = context.user_data.get("cashout_chat_id")
    if club_id and user_id and chat_id:
        try:
            record_activity(club_id, user_id, chat_id, "cashout")
        except Exception:
            pass


def _cleanup(context):
    for key in (
        "cashout_club_id",
        "cashout_chat_id",
        "cashout_user_id",
        "cashout_amount",
        "cashout_selected",
        "cashout_current_method",
        "cashout_multi",
        "cashout_simple_data",
    ):
        context.user_data.pop(key, None)


async def cashout_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    club_id = context.user_data.get("cashout_club_id")
    user_id = context.user_data.get("cashout_user_id")
    if club_id and user_id:
        try:
            cancel_last_cashout_activity(club_id, user_id)
        except Exception:
            pass
    if update.message:
        await update.message.reply_text("Cashout cancelled.")
    _cleanup(context)
    return ConversationHandler.END


async def cashout_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.user_data.get("cashout_chat_id")
    if chat_id:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "We didn't hear back from you so we are canceling your request! "
                    "Please use /cashout to cashout again!"
                ),
            )
        except Exception:
            pass
    _cleanup(context)


TIMEOUT_SECONDS = 600


def get_cashout_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("cashout", cashout_entry)],
        states={
            CASHOUT_AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, cashout_amount_received
                ),
            ],
            CASHOUT_SIMPLE_AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, cashout_simple_amount_received
                ),
            ],
            CASHOUT_CHOOSE: [
                CallbackQueryHandler(cashout_method_chosen, pattern=r"^co:\d+$"),
                CallbackQueryHandler(cashout_method_chosen, pattern=r"^codone$"),
            ],
            CASHOUT_SUB: [
                CallbackQueryHandler(cashout_sub_chosen, pattern=r"^cosub:\d+$"),
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, cashout_timeout),
            ],
        },
        fallbacks=[CommandHandler("cancel", cashout_cancel)],
        conversation_timeout=TIMEOUT_SECONDS,
        name="cashout_conv",
        per_chat=True,
        per_user=True,
    )
