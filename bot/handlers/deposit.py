"""Deposit conversation: amount first, then filtered method selection, then optional crypto sub-option."""

import logging
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
    record_method_deposit,
)
from bot.services.mtproto_group_rename import rename_support_group_title
from bot.services.player_details import merge_union_prefix
from bot.services.round_table_unions import (
    ROUND_TABLE_DEPOSIT_UNIONS,
    is_round_table_club,
    union_label_for_shorthand,
)
from bot.handlers.response_utils import send_response_messages
from bot.services.stripe_deposit import (
    create_stripe_checkout_session,
    stripe_configured,
)
from db.connection import get_db
from db.models import Club

logger = logging.getLogger(__name__)

DEPOSIT_REFERRAL, DEPOSIT_AMOUNT, DEPOSIT_UNION, DEPOSIT_CHOOSE, DEPOSIT_SUB = range(5)


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
    is_bot_admin = user_id in ADMIN_USER_IDS

    if is_bot_admin:
        if not get_club_allows_admin_commands(club_id):
            return ConversationHandler.END
        context.chat_data["deposit_club_id"] = club_id
        context.chat_data["deposit_chat_id"] = chat.id
        context.chat_data["deposit_admin_initiated"] = True
        context.chat_data["deposit_admin_user_id"] = user_id

        simple = get_club_simple_mode(club_id, "deposit")
        if simple:
            await _send_simple_response(update.message, simple)
            _cleanup(context)
            return ConversationHandler.END

        await update.message.reply_text("How much would you like to deposit?")
        return DEPOSIT_AMOUNT

    claimed = is_first_deposit_claimed(chat.id)
    first = False if claimed else is_first_deposit(club_id, user_id)
    settings = get_first_deposit_settings(club_id) if first else None

    context.chat_data["deposit_club_id"] = club_id
    context.chat_data["deposit_chat_id"] = chat.id
    context.chat_data["deposit_user_id"] = user_id
    context.chat_data["deposit_is_first"] = first
    context.chat_data["deposit_fd_settings"] = settings

    simple = get_club_simple_mode(club_id, "deposit")
    if simple:
        context.chat_data["deposit_simple_data"] = simple

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

    simple = context.chat_data.get("deposit_simple_data")
    if simple:
        return await _finish_simple_deposit(update.message, context)

    await update.message.reply_text("How much would you like to deposit?")
    return DEPOSIT_AMOUNT


async def deposit_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return ConversationHandler.END

    admin_uid = context.chat_data.get("deposit_admin_user_id")
    if admin_uid and update.effective_user and update.effective_user.id == admin_uid:
        return DEPOSIT_AMOUNT

    if context.chat_data.get("deposit_admin_initiated") and update.effective_user:
        context.chat_data["deposit_user_id"] = update.effective_user.id

    club_id = context.chat_data.get("deposit_club_id")
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

    context.chat_data["deposit_amount"] = amount
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

    if is_round_table_club(int(club_id)):
        await _prompt_deposit_union(update.message, context)
        return DEPOSIT_UNION

    await _prompt_deposit_methods(update.message, context, amount=amount)
    return DEPOSIT_CHOOSE


async def deposit_union_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if not data.startswith("depunion:"):
        return DEPOSIT_UNION

    shorthand = data.split(":", 1)[1].strip().upper()
    label = union_label_for_shorthand(shorthand)
    if not label:
        await query.edit_message_text("That option is no longer available.")
        return ConversationHandler.END

    context.chat_data["deposit_union_shorthand"] = shorthand
    context.chat_data["deposit_union_label"] = label

    amount = context.chat_data.get("deposit_amount")
    if not isinstance(amount, Decimal):
        await query.edit_message_text("Deposit session expired. Use /deposit again.")
        _cleanup(context)
        return ConversationHandler.END

    await _prompt_deposit_methods(
        query.message,
        context,
        amount=amount,
        edit_message=query,
    )
    return DEPOSIT_CHOOSE


def _deposit_method_buttons(methods) -> list[list[InlineKeyboardButton]]:
    buttons = []
    row = []
    for m in methods:
        row.append(InlineKeyboardButton(m["name"], callback_data=f"dep:{m['id']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return buttons


async def _prompt_deposit_union(message, context) -> None:
    buttons = [
        [
            InlineKeyboardButton(
                union["label"], callback_data=f"depunion:{union['shorthand']}"
            )
        ]
        for union in ROUND_TABLE_DEPOSIT_UNIONS
    ]
    await message.reply_text(
        "Which club would you like your deposit to be added to?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _prompt_deposit_methods(
    message,
    context,
    *,
    amount: Decimal,
    edit_message=None,
) -> None:
    club_id = context.chat_data.get("deposit_club_id")
    methods = get_methods_for_amount(club_id, "deposit", amount)
    text = f"Deposit amount: ${amount}\nSelect your deposit method:"
    markup = InlineKeyboardMarkup(_deposit_method_buttons(methods))
    if edit_message is not None:
        await edit_message.edit_message_text(text, reply_markup=markup)
    else:
        await message.reply_text(text, reply_markup=markup)


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

    context.chat_data["deposit_method_name"] = method["name"]
    context.chat_data["deposit_method_id"] = method_id

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

    amount = context.chat_data.get("deposit_amount", "?")
    tier = get_tier_for_amount(method_id, amount) if isinstance(amount, Decimal) else None
    if tier:
        response_data = pick_variant(method_id, tier_id=tier["id"]) or tier
    else:
        response_data = pick_variant(method_id) or method
    await _send_deposit_method_response(
        query,
        context,
        amount=amount,
        display_name=method["name"],
        method_id=method_id,
        method_slug=method.get("slug") or "",
        response_data=response_data,
    )
    if isinstance(amount, Decimal):
        try:
            record_method_deposit(method_id, amount)
        except Exception:
            pass
    return await _complete_deposit_flow(query.message.chat, context)


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

    amount = context.chat_data.get("deposit_amount", "?")
    method_name = context.chat_data.get("deposit_method_name", "")
    display = f"{method_name} — {sub['name']}"

    parent_method_id = context.chat_data.get("deposit_method_id")
    parent_slug = ""
    if parent_method_id:
        parent = get_method_by_id(int(parent_method_id))
        if parent:
            parent_slug = parent.get("slug") or ""

    # Stripe Checkout is triggered by the parent method slug "stripe", or by known
    # Stripe-related sub-option slugs.
    effective_slug = parent_slug
    sub_slug = (sub.get("slug") or "").strip().lower()
    if sub_slug in (
        "applepay",
        "apple-pay",
        "apple_pay",
        "debit-card",
        "debit_card",
        "debitcard",
    ):
        effective_slug = "stripe"

    await _send_deposit_method_response(
        query,
        context,
        amount=amount,
        display_name=display,
        method_id=int(parent_method_id) if parent_method_id else None,
        method_slug=effective_slug,
        response_data=sub,
    )
    method_id = parent_method_id
    if method_id and isinstance(amount, Decimal):
        try:
            record_method_deposit(method_id, amount)
        except Exception:
            pass
    return await _complete_deposit_flow(query.message.chat, context)


async def _finish_simple_deposit(message, context):
    """Handle simple-mode deposit: send response, record, bonus message."""
    simple = context.chat_data.get("deposit_simple_data")
    club_id = context.chat_data.get("deposit_club_id")
    user_id = context.chat_data.get("deposit_user_id")
    chat_id = context.chat_data.get("deposit_chat_id")

    if simple:
        await _send_simple_response(message, simple)

    try:
        record_activity(club_id, user_id, chat_id, "deposit")
    except Exception:
        pass

    first = context.chat_data.get("deposit_is_first", False)
    settings = context.chat_data.get("deposit_fd_settings")
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


async def _send_union_chips_message(chat, context) -> None:
    label = context.chat_data.get("deposit_union_label")
    if not label:
        return
    try:
        await chat.send_message(f"Your chips will be added to {label}!")
    except Exception:
        pass


async def _maybe_rename_group_for_union(context: ContextTypes.DEFAULT_TYPE) -> None:
    shorthand = context.chat_data.get("deposit_union_shorthand")
    chat_id = context.chat_data.get("deposit_chat_id")
    club_id = context.chat_data.get("deposit_club_id")
    if not shorthand or not chat_id or not club_id:
        return

    current_title = None
    try:
        chat = await context.bot.get_chat(int(chat_id))
        current_title = chat.title
    except Exception:
        logger.warning(
            "deposit union rename: could not fetch chat title chat_id=%s",
            chat_id,
            exc_info=True,
        )
        return

    new_title = merge_union_prefix(current_title, str(shorthand))
    if not new_title:
        return

    ok = await rename_support_group_title(
        int(chat_id),
        int(club_id),
        new_title,
        bot=context.bot,
        current_title=current_title,
    )
    if not ok:
        logger.warning(
            "deposit union rename failed chat_id=%s new_title=%r",
            chat_id,
            new_title,
        )


async def _complete_deposit_flow(chat, context: ContextTypes.DEFAULT_TYPE):
    _record_deposit(context)
    await _send_union_chips_message(chat, context)
    await _maybe_rename_group_for_union(context)
    await _send_bonus_message(chat, context)
    _cleanup(context)
    return ConversationHandler.END


async def _send_bonus_message(chat, context):
    """If this is a first deposit and bonus is enabled, send the bonus announcement."""
    first = context.chat_data.get("deposit_is_first", False)
    settings = context.chat_data.get("deposit_fd_settings")
    if not first or not settings or not settings["bonus_enabled"] or settings["bonus_pct"] <= 0:
        return
    amount = context.chat_data.get("deposit_amount")
    if not isinstance(amount, Decimal):
        return
    pct = settings["bonus_pct"]
    bonus = (amount * pct / 100).quantize(Decimal("0.01"))
    cap = settings.get("bonus_cap")
    if cap is not None and bonus > cap:
        bonus = cap.quantize(Decimal("0.01"))
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
    club_id = context.chat_data.get("deposit_club_id")
    user_id = context.chat_data.get("deposit_user_id")
    chat_id = context.chat_data.get("deposit_chat_id")
    if club_id and user_id and chat_id:
        try:
            record_activity(club_id, user_id, chat_id, "deposit")
        except Exception:
            pass


async def _send_deposit_method_response(
    query,
    context,
    *,
    amount,
    display_name: str,
    method_id: int | None,
    method_slug: str,
    response_data: dict,
) -> None:
    """Stripe slug: unique Checkout link; otherwise static dashboard response."""
    slug = (method_slug or "").strip().lower()
    stripe_like_slugs = {
        "stripe",
        "applepay",
        "apple-pay",
        "apple_pay",
        "debitcard",
        "debit-card",
        "debit_card",
    }
    is_stripe_like = slug in stripe_like_slugs

    if is_stripe_like and not stripe_configured():
        club_id = context.chat_data.get("deposit_club_id")
        if club_id is not None:
            await _notify_missing_stripe_secret(context, int(club_id))

    if is_stripe_like and stripe_configured() and isinstance(amount, Decimal):
        # Hard cap for card payments.
        if amount > Decimal("100"):
            await query.edit_message_text(
                "Maximum for Apple Pay / Debit Card is $100. Please enter a smaller amount."
            )
            return

        club_id = context.chat_data.get("deposit_club_id")
        chat_id = context.chat_data.get("deposit_chat_id") or query.message.chat.id
        if club_id is not None:
            try:
                group_title = getattr(query.message.chat, "title", None)
                result = create_stripe_checkout_session(
                    telegram_chat_id=int(chat_id),
                    club_id=int(club_id),
                    amount=amount,
                    payment_method_id=method_id,
                    group_title=group_title,
                )
                announcement = f"Deposit request for ${amount} via {display_name}"
                await query.edit_message_text(announcement)
                # Do not send the dashboard-configured response for stripe-like methods.
                # We send a standardized message with the unique Checkout Session link.
                pay_text = (
                    "🚨 NO CREDIT CARDS. They will be refunded immediately\n\n"
                    "• Once sent, please inform us, and an agent will confirm the transaction and add your chips within 2 minutes!\n\n"
                    "• Just post a screenshot of your transaction, and it will be credited to your account!\n\n"
                    f'<a href="{result.checkout_url}">Pay here</a>'
                )
                await query.message.chat.send_message(
                    pay_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                return
            except Exception:
                logger.exception(
                    "stripe checkout failed chat_id=%s method_id=%s",
                    chat_id,
                    method_id,
                )
    await _send_response(query, response_data, amount, display_name)


async def _notify_missing_stripe_secret(context, club_id: int) -> None:
    """DM the club owner when Stripe is selected but STRIPE_SECRET_KEY isn't set."""
    try:
        with get_db() as session:
            club = session.query(Club).filter(Club.id == int(club_id)).one_or_none()
            if not club:
                return
            admin_id = int(club.telegram_user_id)
    except Exception:
        return

    # Basic per-process throttle to avoid spamming the same owner.
    key = f"stripe_secret_missing_notified:{club_id}"
    if context.bot_data.get(key):
        return
    context.bot_data[key] = True

    try:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                f"[{club.name}] Stripe Checkout is not configured on the bot worker.\n"
                "Stripe deposits are currently using the static dashboard text instead of a unique checkout link.\n\n"
                "Fix: set STRIPE_SECRET_KEY on the worker environment and restart."
            ),
        )
    except Exception:
        return


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
        "deposit_method_id",
        "deposit_is_first",
        "deposit_fd_settings",
        "deposit_simple_data",
        "deposit_admin_initiated",
        "deposit_admin_user_id",
        "deposit_union_shorthand",
        "deposit_union_label",
    ):
        context.chat_data.pop(key, None)


async def deposit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("Deposit cancelled.")
    _cleanup(context)
    return ConversationHandler.END


async def deposit_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.chat_data.get("deposit_chat_id")
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
            DEPOSIT_UNION: [
                CallbackQueryHandler(
                    deposit_union_chosen, pattern=r"^depunion:(RT|AT)$"
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
        per_user=False,
    )
