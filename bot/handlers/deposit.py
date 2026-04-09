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
    pick_variant,
    is_first_deposit,
    is_first_deposit_claimed,
    get_first_deposit_settings,
    update_group_name,
)
from bot.handlers.response_utils import send_response_messages

DEPOSIT_REFERRAL, DEPOSIT_AMOUNT, DEPOSIT_CHOOSE, DEPOSIT_SUB = range(4)


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

    update_group_name(chat.id, chat.title)

    user_id = update.effective_user.id
    if user_id in ADMIN_USER_IDS and not get_club_allows_admin_commands(club_id):
        return ConversationHandler.END

    claimed = is_first_deposit_claimed(chat.id)
    first = False if claimed else is_first_deposit(club_id, user_id)
    settings = get_first_deposit_settings(club_id) if first else None

    context.user_data["deposit_club_id"] = club_id
    context.user_data["deposit_chat_id"] = chat.id
    context.user_data["deposit_user_id"] = user_id
    context.user_data["deposit_is_first"] = first
    context.user_data["deposit_fd_settings"] = settings

    simple = get_club_simple_mode(club_id, "deposit")
    if simple:
        context.user_data["deposit_simple_data"] = simple

    if first and settings and settings["referral_enabled"]:
        await update.message.reply_text(
            "Welcome to the club! How did you hear about us? "
            "If it was a player, please type their GG username."
        )
        return DEPOSIT_REFERRAL

    if simple:
        return await _finish_simple_deposit(update.message, context)

    await update.message.reply_text("How much would you like to deposit?")
    return DEPOSIT_AMOUNT


async def deposit_referral_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END

    simple = context.user_data.get("deposit_simple_data")
    if simple:
        return await _finish_simple_deposit(update.message, context)

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
    if tier:
        response_data = pick_variant(method_id, tier_id=tier["id"]) or tier
    else:
        response_data = pick_variant(method_id) or method
    await _send_response(query, response_data, amount, method["name"])
    _record_deposit(context)
    await _send_bonus_message(query.message.chat, context)
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
    await _send_bonus_message(query.message.chat, context)
    _cleanup(context)
    return ConversationHandler.END


async def _finish_simple_deposit(message, context):
    """Handle simple-mode deposit: send response, record, bonus message."""
    simple = context.user_data.get("deposit_simple_data")
    club_id = context.user_data.get("deposit_club_id")
    user_id = context.user_data.get("deposit_user_id")
    chat_id = context.user_data.get("deposit_chat_id")

    if simple:
        await _send_simple_response(message, simple)

    try:
        record_activity(club_id, user_id, chat_id, "deposit")
    except Exception:
        pass

    first = context.user_data.get("deposit_is_first", False)
    settings = context.user_data.get("deposit_fd_settings")
    if first and settings and settings["bonus_enabled"] and settings["bonus_pct"] > 0:
        try:
            await message.reply_text(
                f"Since this is your first deposit, you will get a "
                f"{settings['bonus_pct']}% first deposit bonus on us!\n\n"
                f"This must be your first time depositing in the club to be eligible for this bonus."
            )
        except Exception:
            pass

    _cleanup(context)
    return ConversationHandler.END


async def _send_bonus_message(chat, context):
    """If this is a first deposit and bonus is enabled, send the bonus announcement."""
    first = context.user_data.get("deposit_is_first", False)
    settings = context.user_data.get("deposit_fd_settings")
    if not first or not settings or not settings["bonus_enabled"] or settings["bonus_pct"] <= 0:
        return
    amount = context.user_data.get("deposit_amount")
    if not isinstance(amount, Decimal):
        return
    pct = settings["bonus_pct"]
    bonus = (amount * pct / 100).quantize(Decimal("0.01"))
    try:
        await chat.send_message(
            f"Since this is your first deposit, you will get a "
            f"{pct}% first deposit bonus of ${bonus:,.2f} added to "
            f"your deposit of ${amount:,.2f} on us!\n\n"
            f"This must be your first time depositing in the club to be eligible for this bonus."
        )
    except Exception:
        pass


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
        "deposit_is_first",
        "deposit_fd_settings",
        "deposit_simple_data",
    ):
        context.user_data.pop(key, None)


async def deposit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("Deposit cancelled.")
    _cleanup(context)
    return ConversationHandler.END


async def deposit_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.user_data.get("deposit_chat_id")
    if chat_id:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "We didn't hear back from you so we are canceling your request! "
                    "Please use /deposit to deposit again!"
                ),
            )
        except Exception:
            pass
    _cleanup(context)


TIMEOUT_SECONDS = 600


def get_deposit_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("deposit", deposit_entry)],
        states={
            DEPOSIT_REFERRAL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, deposit_referral_received
                ),
            ],
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
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, deposit_timeout),
            ],
        },
        fallbacks=[CommandHandler("cancel", deposit_cancel)],
        conversation_timeout=TIMEOUT_SECONDS,
        name="deposit_conv",
        per_chat=True,
        per_user=True,
    )
