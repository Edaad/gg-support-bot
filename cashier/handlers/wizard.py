"""GGCashier cashout wizard conversation (staff DM)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.services.club import (
    get_club_for_chat,
    get_lowest_minimum,
    get_method_by_id,
    get_methods_for_amount,
    get_sub_option_by_id,
    get_sub_options,
)
from bot.services.method_resolution import resolve_method_display
from bot.services.player_details import (
    parse_tracking_title,
    resolve_club_id_from_shorthand,
)
from cashier.handlers.auth import can_access_job, can_use_cashier
from cashier.services.complete import complete_cashout_job
from cashier.services.jobs import cancel_job, get_job, mark_in_progress, update_job

(
    GC_TITLE,
    GC_AMOUNT,
    GC_CONFIRM,
    GC_TRADE,
    GC_COOLDOWN,
    GC_METHOD,
    GC_SUB,
    GC_PAYOUT,
    GC_CONFIRM_DETAILS,
) = range(9)

TIMEOUT_SECONDS = 900


def _format_amount(amount: Decimal) -> str:
    if amount == amount.to_integral_value():
        return f"${int(amount):,}"
    return f"${amount:,.2f}"


def _load_job_into_user_data(context: ContextTypes.DEFAULT_TYPE, job: dict) -> None:
    context.user_data["gc_job_id"] = job["id"]
    context.user_data["gc_club_id"] = job["club_id"]
    context.user_data["gc_chat_id"] = job["chat_id"]
    context.user_data["gc_group_title"] = job["group_title"]
    context.user_data["gc_amount"] = job["amount"]
    if not isinstance(context.user_data["gc_amount"], Decimal):
        context.user_data["gc_amount"] = Decimal(str(context.user_data["gc_amount"]))


def _cleanup_user_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in list(context.user_data.keys()):
        if key.startswith("gc_"):
            context.user_data.pop(key, None)


async def _send_cancelled(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    job_id = context.user_data.get("gc_job_id")
    if job_id:
        cancel_job(int(job_id))
    _cleanup_user_data(context)
    msg = "Cashout cancelled."
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg)
    elif update.message:
        await update.message.reply_text(msg)
    return ConversationHandler.END


async def _show_confirm_amount(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit: bool = False
) -> int:
    title = context.user_data.get("gc_group_title", "Unknown")
    amount = context.user_data["gc_amount"]
    text = (
        f"Confirm amount\n"
        f"Group: {title}\n"
        f"Amount: {_format_amount(amount)}"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("CONFIRM", callback_data="gc_confirm"),
                InlineKeyboardButton("MODIFY", callback_data="gc_modify"),
            ],
            [InlineKeyboardButton("CANCEL", callback_data="gc_cancel")],
        ]
    )
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=keyboard)
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard)
    return GC_CONFIRM


async def cashout_dm_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    if update.effective_chat.type != "private":
        await update.message.reply_text("Use /cashout in a private chat with GGCashier.")
        return ConversationHandler.END

    _cleanup_user_data(context)
    context.user_data["gc_trigger"] = "dm_cashout"
    context.user_data["gc_staff_id"] = update.effective_user.id
    await update.message.reply_text(
        "Paste the support group title\n"
        "(Example: RT / 2427-3267 / Samin)"
    )
    return GC_TITLE


async def job_callback_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query or not update.effective_user:
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if not data.startswith("gc_job:"):
        return ConversationHandler.END

    job_id = int(data.split(":")[1])
    job = get_job(job_id)
    if not job:
        await query.edit_message_text("This cashout job is no longer available.")
        return ConversationHandler.END

    if job["status"] in ("completed", "cancelled"):
        await query.edit_message_text(f"This cashout was already {job['status']}.")
        return ConversationHandler.END

    user_id = update.effective_user.id
    if not can_access_job(user_id, int(job["initiated_by"]), int(job["club_id"])):
        await query.edit_message_text("You cannot access this cashout job.")
        return ConversationHandler.END

    _cleanup_user_data(context)
    _load_job_into_user_data(context, job)
    context.user_data["gc_staff_id"] = user_id
    mark_in_progress(job_id)
    return await _show_confirm_amount(update, context, edit=True)


async def title_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END

    title = (update.message.text or "").strip()
    parsed = parse_tracking_title(title)
    if not parsed:
        await update.message.reply_text(
            "Invalid group title format. Use: CLUB / PLAYER_ID / NAME\n"
            "(Example: RT / 2427-3267 / Samin)"
        )
        return GC_TITLE

    shorthand, gg_player_id = parsed
    club_id = resolve_club_id_from_shorthand(shorthand)
    if not club_id:
        await update.message.reply_text("Unknown club in group title.")
        return GC_TITLE

    staff_id = context.user_data.get("gc_staff_id")
    if staff_id and not can_use_cashier(int(staff_id), club_id):
        await update.message.reply_text("You are not authorized for this club.")
        _cleanup_user_data(context)
        return ConversationHandler.END

    from sqlalchemy import text

    from db.connection import get_db

    chat_id = None
    with get_db() as session:
        row = session.execute(
            text(
                """
                SELECT chat_ids
                FROM player_details
                WHERE club_id = :club_id AND gg_player_id = :gg_player_id
                LIMIT 1
                """
            ),
            {"club_id": club_id, "gg_player_id": gg_player_id},
        ).fetchone()
        if row and row[0]:
            chat_ids = [int(c) for c in row[0]]
            for cid in chat_ids:
                if get_club_for_chat(cid) == club_id:
                    chat_id = cid
                    break
            if chat_id is None and chat_ids:
                chat_id = chat_ids[0]

    if chat_id is None:
        await update.message.reply_text(
            "Could not find a linked support group for this player. "
            "Ensure the group is linked to the club and tracked."
        )
        return GC_TITLE

    linked_club = get_club_for_chat(chat_id)
    if linked_club != club_id:
        await update.message.reply_text("Group is not linked to the expected club.")
        return GC_TITLE

    context.user_data["gc_club_id"] = club_id
    context.user_data["gc_chat_id"] = chat_id
    context.user_data["gc_group_title"] = title

    await update.message.reply_text("Enter the cashout amount:")
    return GC_AMOUNT


async def amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END

    raw = (update.message.text or "").strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(raw)
        if amount <= 0:
            raise InvalidOperation()
    except (InvalidOperation, Exception):
        await update.message.reply_text(
            "Please enter a valid dollar amount (Example: 500 or 100.00)."
        )
        return GC_AMOUNT

    context.user_data["gc_amount"] = amount

    trigger = context.user_data.get("gc_trigger", "dm_cashout")
    staff_id = context.user_data.get("gc_staff_id")
    if trigger == "dm_cashout" and staff_id:
        from cashier.services.jobs import create_job

        job = create_job(
            club_id=int(context.user_data["gc_club_id"]),
            chat_id=int(context.user_data["gc_chat_id"]),
            group_title=context.user_data["gc_group_title"],
            amount=amount,
            initiated_by=int(staff_id),
            trigger="dm_cashout",
        )
        context.user_data["gc_job_id"] = job["id"]
        mark_in_progress(job["id"])

    return await _show_confirm_amount(update, context)


async def confirm_amount_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "gc_cancel":
        return await _send_cancelled(update, context)

    if data == "gc_modify":
        await query.edit_message_text("Enter the new cashout amount:")
        return GC_AMOUNT

    if data != "gc_confirm":
        return GC_CONFIRM

    job_id = context.user_data.get("gc_job_id")
    if job_id:
        update_job(int(job_id), amount=context.user_data["gc_amount"])

    text = (
        f"Trade record checked?\n"
        f"Group: {context.user_data.get('gc_group_title')}\n"
        f"Amount: {_format_amount(context.user_data['gc_amount'])}"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("YES", callback_data="gc_trade_yes"),
                InlineKeyboardButton("CANCEL", callback_data="gc_cancel"),
            ]
        ]
    )
    await query.edit_message_text(text, reply_markup=keyboard)
    return GC_TRADE


async def trade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "gc_cancel":
        return await _send_cancelled(update, context)

    if data != "gc_trade_yes":
        return GC_TRADE

    job_id = context.user_data.get("gc_job_id")
    if job_id:
        update_job(int(job_id), trade_record_checked=True)

    text = (
        f"24-hour rule checked?\n"
        f"Group: {context.user_data.get('gc_group_title')}\n"
        f"Amount: {_format_amount(context.user_data['gc_amount'])}"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("YES", callback_data="gc_cooldown_yes"),
                InlineKeyboardButton("CANCEL", callback_data="gc_cancel"),
            ]
        ]
    )
    await query.edit_message_text(text, reply_markup=keyboard)
    return GC_COOLDOWN


async def cooldown_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "gc_cancel":
        return await _send_cancelled(update, context)

    if data != "gc_cooldown_yes":
        return GC_COOLDOWN

    job_id = context.user_data.get("gc_job_id")
    if job_id:
        update_job(int(job_id), cooldown_checked=True)

    return await _show_method_keyboard(update, context)


async def _show_method_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    club_id = int(context.user_data["gc_club_id"])
    amount = context.user_data["gc_amount"]
    title = context.user_data.get("gc_group_title", "")

    methods = get_methods_for_amount(club_id, "cashout", amount)

    if not methods:
        lowest = get_lowest_minimum(club_id, "cashout")
        if lowest is not None and amount < lowest:
            msg = f"Sorry! The minimum cashout amount is ${lowest:,.2f}."
        else:
            msg = (
                f"No cashout methods available for {_format_amount(amount)}. "
                f"Go back and modify the amount, or cancel."
            )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("MODIFY", callback_data="gc_modify"),
                    InlineKeyboardButton("CANCEL", callback_data="gc_cancel"),
                ]
            ]
        )
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=keyboard)
        return GC_CONFIRM

    buttons = []
    row = []
    for m in methods:
        row.append(
            InlineKeyboardButton(m["name"], callback_data=f"gc_m:{m['id']}")
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("CANCEL", callback_data="gc_cancel")])

    text = (
        f"Select payout method\n"
        f"Group: {title} · Amount: {_format_amount(amount)}\n"
        f"Choose how this cashout will be sent."
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(buttons)
        )
    return GC_METHOD


async def method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "gc_cancel":
        return await _send_cancelled(update, context)

    if not data.startswith("gc_m:"):
        return GC_METHOD

    method_id = int(data.split(":")[1])
    method = get_method_by_id(method_id)
    if not method:
        await query.edit_message_text("That method is no longer available.")
        return ConversationHandler.END

    context.user_data["gc_method_id"] = method_id

    if method["has_sub_options"]:
        subs = get_sub_options(method_id)
        if subs:
            buttons = []
            row = []
            for s in subs:
                row.append(
                    InlineKeyboardButton(
                        s["name"], callback_data=f"gc_sub:{s['id']}"
                    )
                )
                if len(row) == 2:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            buttons.append(
                [InlineKeyboardButton("CANCEL", callback_data="gc_cancel")]
            )
            await query.edit_message_text(
                f"You selected {method['name']}. Which option?",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return GC_SUB

    display, slug = resolve_method_display(
        method_id, context.user_data["gc_amount"]
    )
    context.user_data["gc_method_display_name"] = display
    context.user_data["gc_slug"] = slug

    text = (
        f"Record payout details\n"
        f"Group: {context.user_data.get('gc_group_title')}\n"
        f"Amount: {_format_amount(context.user_data['gc_amount'])}\n"
        f"Method: {display}\n\n"
        f"Enter the player's {method['name']} details for this cashout\n"
        f"(Example: Venmo handle, Zelle email/phone, wallet address, etc.)"
    )
    await query.edit_message_text(text)
    return GC_PAYOUT


async def sub_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "gc_cancel":
        return await _send_cancelled(update, context)

    if not data.startswith("gc_sub:"):
        return GC_SUB

    sub_id = int(data.split(":")[1])
    sub = get_sub_option_by_id(sub_id)
    method_id = context.user_data.get("gc_method_id")
    method = get_method_by_id(int(method_id)) if method_id else None
    if not sub or not method:
        await query.edit_message_text("That option is no longer available.")
        return ConversationHandler.END

    context.user_data["gc_sub_option_id"] = sub_id
    display, slug = resolve_method_display(
        int(method_id), context.user_data["gc_amount"], sub_option_id=sub_id
    )
    context.user_data["gc_method_display_name"] = display
    context.user_data["gc_slug"] = slug

    text = (
        f"Record payout details\n"
        f"Group: {context.user_data.get('gc_group_title')}\n"
        f"Amount: {_format_amount(context.user_data['gc_amount'])}\n"
        f"Method: {display}\n\n"
        f"Enter the player's {method['name']} details for this cashout\n"
        f"(Example: Venmo handle, Zelle email/phone, wallet address, etc.)"
    )
    await query.edit_message_text(text)
    return GC_PAYOUT


async def payout_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END

    details = (update.message.text or "").strip()
    if not details:
        await update.message.reply_text("Please enter payout details.")
        return GC_PAYOUT

    context.user_data["gc_payout_details"] = details
    return await _show_confirm_details(update, context)


async def _show_confirm_details(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit: bool = False
):
    title = context.user_data.get("gc_group_title", "")
    amount = context.user_data["gc_amount"]
    display = context.user_data.get("gc_method_display_name", "")
    details = context.user_data.get("gc_payout_details", "")

    text = (
        f"Confirm payout details\n"
        f"Group: {title}\n"
        f"Amount: {_format_amount(amount)}\n"
        f"Method: {display}\n"
        f"Details: {details}"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "CONFIRM DETAILS", callback_data="gc_details_confirm"
                ),
                InlineKeyboardButton("EDIT", callback_data="gc_details_edit"),
            ],
            [InlineKeyboardButton("CANCEL", callback_data="gc_cancel")],
        ]
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard)
    elif update.callback_query and edit:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    return GC_CONFIRM_DETAILS


async def confirm_details_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "gc_cancel":
        return await _send_cancelled(update, context)

    if data == "gc_details_edit":
        method = get_method_by_id(int(context.user_data["gc_method_id"]))
        name = method["name"] if method else "payment"
        await query.edit_message_text(
            f"Enter the player's {name} details for this cashout:"
        )
        return GC_PAYOUT

    if data != "gc_details_confirm":
        return GC_CONFIRM_DETAILS

    job_id = context.user_data.get("gc_job_id")
    if not job_id:
        await query.edit_message_text("Job session expired.")
        _cleanup_user_data(context)
        return ConversationHandler.END

    update_job(
        int(job_id),
        payment_method_id=context.user_data.get("gc_method_id"),
        payment_sub_option_id=context.user_data.get("gc_sub_option_id"),
        method_display_name=context.user_data.get("gc_method_display_name"),
        payout_details=context.user_data.get("gc_payout_details"),
        trade_record_checked=True,
        cooldown_checked=True,
    )

    ok, err = await complete_cashout_job(int(job_id))
    if not ok:
        await query.edit_message_text(err or "Failed to complete cashout.")
        return ConversationHandler.END

    staff = update.effective_user
    username = f"@{staff.username}" if staff and staff.username else "staff"
    summary = (
        f"Cashout recorded\n"
        f"Group: {context.user_data.get('gc_group_title')}\n"
        f"Amount: {_format_amount(context.user_data['gc_amount'])}\n"
        f"Method: {context.user_data.get('gc_method_display_name')}\n"
        f"Details: {context.user_data.get('gc_payout_details')}\n"
        f"Recorded by {username}"
    )
    await query.edit_message_text(summary)
    _cleanup_user_data(context)
    return ConversationHandler.END


async def wizard_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    job_id = context.user_data.get("gc_job_id")
    if job_id:
        cancel_job(int(job_id))
    _cleanup_user_data(context)


def get_cashier_wizard_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler(
                "cashout", cashout_dm_entry, filters=filters.ChatType.PRIVATE
            ),
            CallbackQueryHandler(job_callback_entry, pattern=r"^gc_job:\d+$"),
        ],
        states={
            GC_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, title_received),
            ],
            GC_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, amount_received),
            ],
            GC_CONFIRM: [
                CallbackQueryHandler(
                    confirm_amount_callback,
                    pattern=r"^gc_(confirm|modify|cancel)$",
                ),
            ],
            GC_TRADE: [
                CallbackQueryHandler(
                    trade_callback, pattern=r"^gc_(trade_yes|cancel)$"
                ),
            ],
            GC_COOLDOWN: [
                CallbackQueryHandler(
                    cooldown_callback, pattern=r"^gc_(cooldown_yes|cancel)$"
                ),
            ],
            GC_METHOD: [
                CallbackQueryHandler(
                    method_chosen, pattern=r"^gc_(m:\d+|cancel)$"
                ),
            ],
            GC_SUB: [
                CallbackQueryHandler(
                    sub_chosen, pattern=r"^gc_(sub:\d+|cancel)$"
                ),
            ],
            GC_PAYOUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, payout_received),
            ],
            GC_CONFIRM_DETAILS: [
                CallbackQueryHandler(
                    confirm_details_callback,
                    pattern=r"^gc_(details_confirm|details_edit|cancel)$",
                ),
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, wizard_timeout),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", _send_cancelled),
            CallbackQueryHandler(_send_cancelled, pattern=r"^gc_cancel$"),
        ],
        conversation_timeout=TIMEOUT_SECONDS,
        name="cashier_wizard",
        per_chat=True,
        per_user=True,
    )
