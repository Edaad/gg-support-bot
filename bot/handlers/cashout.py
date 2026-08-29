"""Cashout conversation: amount first, filtered methods, optional multi-method with inline Done button."""

from decimal import Decimal, InvalidOperation
import logging

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
    get_auto_cashout_enabled,
    get_group_title_for_chat,
    update_group_name,
)
from bot.services.cashout_handle_validation import (
    supported_cashout_slug,
    validate_cashout_handle,
)
from bot.services.round_table_unions import (
    ROUND_TABLE_DEPOSIT_UNIONS,
    is_round_table_club,
    union_label_for_shorthand,
)
from bot.services.deposit_method_access import (
    filter_cashout_methods_for_chat,
    is_cashout_method_allowed_for_chat,
)
from bot.handlers.flow_cancel import (
    block_if_group_money_flow_active,
    clear_active_flow,
    mark_active_flow,
)
from bot.handlers.flow_staleness import (
    AMOUNT_TEXT,
    cashout_amount_actor_allowed,
    handle_stale_flow_callback,
    is_update_too_old,
    log_stale_update,
    looks_like_amount,
    register_flow_callback_message,
    reset_flow_callback_messages,
)
from bot.handlers.response_utils import send_response_messages
from bot.services import popup_keyboard as popup_keyboard_svc

logger = logging.getLogger(__name__)

CASHOUT_AMOUNT, CASHOUT_CHOOSE, CASHOUT_SUB, CASHOUT_SIMPLE_AMOUNT = range(4)
(
    CASHOUT_AUTO_AMOUNT,
    CASHOUT_AUTO_UNION,
    CASHOUT_AUTO_CHOOSE,
    CASHOUT_AUTO_SUB,
    CASHOUT_AUTO_HANDLE,
) = range(4, 9)


def _cashout_amount_prompt_kwargs(context):
    kwargs = {}
    strip = popup_keyboard_svc.pop_strip_reply_markup(context)
    if strip is not None:
        kwargs["reply_markup"] = strip
    return kwargs


async def cashout_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not update.effective_chat or not update.effective_user:
        return ConversationHandler.END
    # Command messages only — idle-help button callbacks use the prompt's age.
    if update.message and is_update_too_old(update):
        log_stale_update(update, handler="cashout_entry")
        return ConversationHandler.END
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await message.reply_text("Use /cashout in a club group.")
        return ConversationHandler.END
    if await block_if_group_money_flow_active(
        update, context, starting="cashout", chat_id=chat.id
    ):
        return ConversationHandler.END
    _cleanup(context)
    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        await message.reply_text(
            "This group isn't linked to a club yet. The club owner must add the bot."
        )
        return ConversationHandler.END

    update_group_name(chat.id, chat.title)

    user_id = update.effective_user.id
    is_bot_admin = user_id in ADMIN_USER_IDS

    if is_bot_admin:
        if not get_club_allows_admin_commands(club_id):
            return ConversationHandler.END
        popup_keyboard_svc.prepare_flow_entry_keyboard(
            context, chat.id, club_id=club_id, title=chat.title
        )
        context.chat_data["cashout_club_id"] = club_id
        context.chat_data["cashout_chat_id"] = chat.id
        context.chat_data["cashout_admin_initiated"] = True
        context.chat_data["cashout_admin_user_id"] = user_id

        simple = get_club_simple_mode(club_id, "cashout")
        if simple:
            max_amt = get_cashout_max_amount(club_id)
            soft_limit = get_cashout_soft_limit(club_id)
            if max_amt is not None or soft_limit is not None:
                context.chat_data["cashout_simple_data"] = simple
                mark_active_flow(context, "cashout")
                await message.reply_text(
                    "How much would you like to cashout?",
                    **_cashout_amount_prompt_kwargs(context),
                )
                return CASHOUT_SIMPLE_AMOUNT
            strip = popup_keyboard_svc.pop_strip_reply_markup(context)
            if strip is not None:
                try:
                    await message.reply_text("\u200b", reply_markup=strip)
                except Exception:
                    pass
            await _send_simple_response(message, simple)
            _cleanup_after_flow(context)
            return ConversationHandler.END

        if get_auto_cashout_enabled(club_id):
            context.chat_data["cashout_auto"] = True
            mark_active_flow(context, "cashout")
            await message.reply_text(
                "How much would you like to cashout?",
                **_cashout_amount_prompt_kwargs(context),
            )
            return CASHOUT_AUTO_AMOUNT

        context.chat_data["cashout_selected"] = []
        context.chat_data["cashout_multi"] = get_club_allows_multi_cashout(club_id)
        mark_active_flow(context, "cashout")
        await message.reply_text(
            "How much would you like to cashout?",
            **_cashout_amount_prompt_kwargs(context),
        )
        return CASHOUT_AMOUNT

    # Cooldown + business hours check (admins/staff are exempt)
    is_staff = is_club_staff(user_id, club_id)
    if not is_staff:
        eligible, deny_msg = check_cashout_eligibility(club_id, chat.id)
        if not eligible:
            await message.reply_text(deny_msg)
            return ConversationHandler.END
        # Auto-mode cashouts are hands-off; skip the "Cash out initiated." ping so
        # admins are only alerted when the flow actually needs a human.
        if not get_auto_cashout_enabled(club_id):
            try:
                from bot.services.escalation_notification import notify_cashout_started

                await notify_cashout_started(
                    club_id=club_id, chat_id=chat.id, title=chat.title
                )
            except Exception:
                pass

    popup_keyboard_svc.prepare_flow_entry_keyboard(
        context, chat.id, club_id=club_id, title=chat.title
    )

    simple = get_club_simple_mode(club_id, "cashout")
    if simple:
        max_amt = get_cashout_max_amount(club_id)
        soft_limit = get_cashout_soft_limit(club_id)
        if max_amt is not None or soft_limit is not None:
            context.chat_data["cashout_club_id"] = club_id
            context.chat_data["cashout_chat_id"] = chat.id
            context.chat_data["cashout_user_id"] = user_id
            context.chat_data["cashout_simple_data"] = simple
            mark_active_flow(context, "cashout")
            await message.reply_text(
                "How much would you like to cashout?",
                **_cashout_amount_prompt_kwargs(context),
            )
            return CASHOUT_SIMPLE_AMOUNT

        strip = popup_keyboard_svc.pop_strip_reply_markup(context)
        if strip is not None:
            try:
                await message.reply_text("\u200b", reply_markup=strip)
            except Exception:
                pass
        await _send_simple_response(message, simple)
        try:
            record_activity(club_id, user_id, chat.id, "cashout")
        except Exception:
            pass
        _cleanup_after_flow(context)
        return ConversationHandler.END

    if get_auto_cashout_enabled(club_id):
        context.chat_data["cashout_club_id"] = club_id
        context.chat_data["cashout_chat_id"] = chat.id
        context.chat_data["cashout_user_id"] = user_id
        context.chat_data["cashout_auto"] = True
        mark_active_flow(context, "cashout")
        await message.reply_text(
            "How much would you like to cashout?",
            **_cashout_amount_prompt_kwargs(context),
        )
        return CASHOUT_AUTO_AMOUNT

    context.chat_data["cashout_club_id"] = club_id
    context.chat_data["cashout_chat_id"] = chat.id
    context.chat_data["cashout_user_id"] = user_id
    context.chat_data["cashout_selected"] = []
    context.chat_data["cashout_multi"] = get_club_allows_multi_cashout(club_id)
    mark_active_flow(context, "cashout")
    await message.reply_text(
        "How much would you like to cashout?",
        **_cashout_amount_prompt_kwargs(context),
    )
    return CASHOUT_AMOUNT


async def cashout_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END
    if is_update_too_old(update):
        log_stale_update(update, handler="cashout_amount_received")
        _cleanup_after_flow(context)
        return ConversationHandler.END

    sender_id = update.effective_user.id if update.effective_user else None
    message_text = update.message.text or ""
    if not cashout_amount_actor_allowed(
        context, sender_id=sender_id, text=message_text
    ):
        return CASHOUT_AMOUNT

    # In admin-initiated flows, only record a non-admin as the cashouter
    # and only run cooldown checks when a customer responds.
    if context.chat_data.get("cashout_admin_initiated") and update.effective_user:
        uid = update.effective_user.id
        if uid not in ADMIN_USER_IDS:
            context.chat_data["cashout_user_id"] = uid
            club_id = context.chat_data.get("cashout_club_id")
            cashout_chat_id = context.chat_data.get("cashout_chat_id")
            if club_id and cashout_chat_id:
                if not is_club_staff(uid, club_id):
                    eligible, deny_msg = check_cashout_eligibility(
                        club_id, cashout_chat_id
                    )
                    if not eligible:
                        await update.message.reply_text(deny_msg)
                        _cleanup_after_flow(context)
                        return ConversationHandler.END

    club_id = context.chat_data.get("cashout_club_id")
    if not club_id:
        return ConversationHandler.END

    raw = message_text.strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(raw)
        if amount <= 0:
            raise InvalidOperation()
    except (InvalidOperation, Exception):
        if looks_like_amount(message_text):
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

    context.chat_data["cashout_amount"] = amount
    try:
        from bot.services.support_group_idle_episode import mark_expected_flow_input

        mark_expected_flow_input(context)
    except Exception:
        pass
    return await _show_method_keyboard(update, context, first_pick=True)


async def cashout_simple_amount_received(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return ConversationHandler.END
    if is_update_too_old(update):
        log_stale_update(update, handler="cashout_simple_amount_received")
        _cleanup_after_flow(context)
        return ConversationHandler.END

    sender_id = update.effective_user.id if update.effective_user else None
    message_text = update.message.text or ""
    if not cashout_amount_actor_allowed(
        context, sender_id=sender_id, text=message_text
    ):
        return CASHOUT_SIMPLE_AMOUNT

    if context.chat_data.get("cashout_admin_initiated") and update.effective_user:
        uid = update.effective_user.id
        if uid not in ADMIN_USER_IDS:
            context.chat_data["cashout_user_id"] = uid
            club_id = context.chat_data.get("cashout_club_id")
            cashout_chat_id = context.chat_data.get("cashout_chat_id")
            if club_id and cashout_chat_id:
                if not is_club_staff(uid, club_id):
                    eligible, deny_msg = check_cashout_eligibility(
                        club_id, cashout_chat_id
                    )
                    if not eligible:
                        await update.message.reply_text(deny_msg)
                        _cleanup_after_flow(context)
                        return ConversationHandler.END

    club_id = context.chat_data.get("cashout_club_id")
    if not club_id:
        return ConversationHandler.END

    raw = message_text.strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(raw)
        if amount <= 0:
            raise InvalidOperation()
    except (InvalidOperation, Exception):
        if looks_like_amount(message_text):
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

    lowest = get_lowest_minimum(club_id, "cashout")
    if lowest is not None and amount < lowest:
        await update.message.reply_text(
            f"Sorry! The minimum cashout amount is ${lowest:,.2f}."
        )
        return CASHOUT_SIMPLE_AMOUNT

    simple_data = context.chat_data.get("cashout_simple_data")
    user_id = context.chat_data.get("cashout_user_id")
    chat_id = context.chat_data.get("cashout_chat_id")
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
    try:
        from bot.services.support_group_idle_episode import mark_expected_flow_input

        mark_expected_flow_input(context)
    except Exception:
        pass
    _cleanup_after_flow(context)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Automated cashout (per-club enable_auto_cashout): claim chips, collect a
# validated payout handle, and record to the hub. Anything off-script escalates
# with a single "an agent will be with you shortly" and the bot bows out.
# ---------------------------------------------------------------------------

AUTO_CLAIMING_COPY = "Claiming chips...this will just take a minute!"
AUTO_AGENT_SHORTLY_COPY = "An agent will be with you shortly."

_AUTO_HANDLE_PROMPTS = {
    "venmo": "Please reply with your Venmo @username or Venmo link.",
    "cashapp": "Please reply with your Cash App $cashtag or Cash App link.",
    "zelle": "Please reply with your Zelle phone number or email.",
    "paypal": "Please reply with your PayPal email or PayPal.me link.",
    "crypto": "Please reply with your {asset} wallet address.",
}


def _auto_target_id(context):
    return context.chat_data.get("cashout_user_id")


def _auto_sender_is_helper(context, sender_id):
    """True for global admins / club staff who may quietly take over (ignored)."""
    if sender_id is None:
        return True
    if sender_id in ADMIN_USER_IDS:
        return True
    club_id = context.chat_data.get("cashout_club_id")
    if club_id and is_club_staff(sender_id, club_id):
        return True
    return False


async def _auto_escalate(update, context, *, detail):
    """Tell the player an agent is coming (once), Slack-escalate, and END."""
    club_id = context.chat_data.get("cashout_club_id")
    chat_id = context.chat_data.get("cashout_chat_id")
    amount = context.chat_data.get("cashout_amount")
    claimed = bool(context.chat_data.get("cashout_auto_claimed"))
    chat = update.effective_chat
    if chat is not None:
        try:
            await chat.send_message(AUTO_AGENT_SHORTLY_COPY)
        except Exception:
            pass
    title = None
    try:
        if chat_id is not None:
            title, _cid = get_group_title_for_chat(int(chat_id))
    except Exception:
        title = None
    if not title and chat is not None:
        title = chat.title
    try:
        from bot.services.escalation_notification import (
            notify_auto_cashout_escalation,
        )

        await notify_auto_cashout_escalation(
            club_id=club_id,
            chat_id=int(chat_id) if chat_id is not None else 0,
            title=title,
            detail=detail,
            claimed_amount=amount if claimed else None,
        )
    except Exception:
        logger.debug("auto cashout: escalation failed", exc_info=True)
    _cleanup_after_flow(context)
    return ConversationHandler.END


async def cashout_auto_amount_received(update, context):
    if not update.message:
        return CASHOUT_AUTO_AMOUNT
    if is_update_too_old(update):
        log_stale_update(update, handler="cashout_auto_amount_received")
        _cleanup_after_flow(context)
        return ConversationHandler.END

    sender_id = update.effective_user.id if update.effective_user else None
    message_text = update.message.text or ""
    if not cashout_amount_actor_allowed(
        context, sender_id=sender_id, text=message_text
    ):
        return CASHOUT_AUTO_AMOUNT

    # This update is an expected wizard input; keep group_activity from escalating it.
    from bot.services.support_group_idle_episode import mark_expected_flow_input

    mark_expected_flow_input(context)

    if context.chat_data.get("cashout_admin_initiated") and update.effective_user:
        uid = update.effective_user.id
        if uid not in ADMIN_USER_IDS:
            context.chat_data["cashout_user_id"] = uid
            club_id0 = context.chat_data.get("cashout_club_id")
            cashout_chat_id = context.chat_data.get("cashout_chat_id")
            if club_id0 and cashout_chat_id and not is_club_staff(uid, club_id0):
                eligible, deny_msg = check_cashout_eligibility(
                    club_id0, cashout_chat_id
                )
                if not eligible:
                    await update.message.reply_text(deny_msg)
                    _cleanup_after_flow(context)
                    return ConversationHandler.END

    club_id = context.chat_data.get("cashout_club_id")
    if not club_id:
        return ConversationHandler.END

    raw = message_text.strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(raw)
        if amount <= 0:
            raise InvalidOperation()
    except (InvalidOperation, Exception):
        if looks_like_amount(message_text):
            await update.message.reply_text(
                "Please enter a valid dollar amount (Example: 50 or 100.00)."
            )
        return CASHOUT_AUTO_AMOUNT

    max_amt = get_cashout_max_amount(club_id)
    if max_amt is not None and amount > max_amt:
        await update.message.reply_text(
            f"Please enter an amount below ${max_amt:,.2f} as that is our maximum "
            f"cashout amount per day! You can request another cashout for the "
            f"remaining amount after 24 hours."
        )
        return CASHOUT_AUTO_AMOUNT

    # Pre-claim: require at least one automatable eligible method for this amount
    # so we never claim chips we then can't pay out.
    if not _auto_eligible_methods(update, club_id, amount):
        lowest = get_lowest_minimum(club_id, "cashout")
        if lowest is not None and amount < lowest:
            msg = f"Sorry! The minimum cashout amount is ${lowest:,.2f}."
        else:
            msg = (
                f"No cashout methods available for ${amount}. "
                f"Please try a different amount."
            )
        await update.message.reply_text(msg)
        _cleanup_after_flow(context)
        return ConversationHandler.END

    context.chat_data["cashout_amount"] = amount

    if is_round_table_club(int(club_id)):
        await _auto_prompt_union(update.message, context)
        return CASHOUT_AUTO_UNION

    return await _auto_show_methods(update, context)


def _auto_eligible_methods(update, club_id, amount):
    """Active cashout methods for the amount that have an automated handle format."""
    methods = get_methods_for_amount(club_id, "cashout", amount)
    chat = update.effective_chat
    if chat is not None:
        methods = filter_cashout_methods_for_chat(int(chat.id), methods)
    return [m for m in methods if supported_cashout_slug(m.get("slug"))]


async def _auto_prompt_union(message, context):
    buttons = [
        [
            InlineKeyboardButton(
                u["label"], callback_data=f"coautounion:{u['shorthand']}"
            )
        ]
        for u in ROUND_TABLE_DEPOSIT_UNIONS
    ]
    sent = await message.reply_text(
        "Which club would you like to cash out from?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    register_flow_callback_message(context, sent.message_id, flow="cashout")


async def cashout_auto_union_chosen(update, context):
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    if await handle_stale_flow_callback(
        update,
        context,
        flow="cashout",
        handler="cashout_auto_union_chosen",
        cleanup=_cleanup,
    ):
        return ConversationHandler.END
    await query.answer()
    data = query.data or ""
    shorthand = data.split(":", 1)[1].strip().upper() if ":" in data else ""
    if shorthand not in ("RT", "AT"):
        return CASHOUT_AUTO_UNION
    context.chat_data["cashout_union_shorthand"] = shorthand
    label = union_label_for_shorthand(shorthand) or shorthand
    try:
        await query.edit_message_text(f"Cashing out from {label}.")
    except Exception:
        pass
    return await _auto_show_methods(update, context)


async def _auto_run_claim(update, context):
    club_id = context.chat_data.get("cashout_club_id")
    chat_id = context.chat_data.get("cashout_chat_id")
    amount = context.chat_data.get("cashout_amount")
    chat = update.effective_chat
    if chat is not None:
        try:
            await chat.send_message(AUTO_CLAIMING_COPY)
        except Exception:
            pass

    title = None
    try:
        if chat_id is not None:
            title, _cid = get_group_title_for_chat(int(chat_id))
    except Exception:
        title = None
    if not title and chat is not None:
        title = chat.title

    key = context.chat_data.get("cashout_auto_claim_key")
    if not key:
        import uuid

        key = f"auto-cashout-{chat_id}-{uuid.uuid4().hex[:12]}"
        context.chat_data["cashout_auto_claim_key"] = key

    union = context.chat_data.get("cashout_union_shorthand")
    try:
        from bot.services.clubgg_deposit_api import run_auto_claim

        outcome = await run_auto_claim(
            club_id=int(club_id),
            chat_id=int(chat_id),
            job_id=0,
            amount=amount,
            group_title=title,
            union_shorthand=union,
            request_id=key,
        )
    except Exception:
        logger.exception("auto cashout: claim crashed chat_id=%s", chat_id)
        return await _auto_escalate(
            update, context, detail="Auto-claim crashed unexpectedly."
        )

    if not outcome.ok:
        if outcome.status == "uncertain":
            # A claim may have gone through — flag as claimed for the AM.
            context.chat_data["cashout_auto_claimed"] = True
            detail = (
                f"Auto-claim UNCERTAIN: {outcome.reason or 'no detail'} \u2014 may "
                f"have claimed; verify on ClubGG, do not re-claim."
            )
        else:
            detail = f"Auto-claim {outcome.status}: {outcome.reason or 'no detail'}"
        return await _auto_escalate(update, context, detail=detail)

    context.chat_data["cashout_auto_claimed"] = True
    return await _auto_finalize(
        update,
        context,
        payout_details=context.chat_data.get("cashout_auto_payout_details") or "",
    )


async def _auto_show_methods(update, context):
    club_id = context.chat_data.get("cashout_club_id")
    amount = context.chat_data.get("cashout_amount")
    methods = _auto_eligible_methods(update, club_id, amount)
    if not methods:
        return await _auto_escalate(
            update,
            context,
            detail=f"No automatable cashout methods available for ${amount}.",
        )
    buttons = []
    row = []
    for m in methods:
        row.append(InlineKeyboardButton(m["name"], callback_data=f"coauto:{m['id']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    chat = update.effective_chat
    if chat is not None:
        sent = await chat.send_message(
            "Select your cashout method:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        register_flow_callback_message(context, sent.message_id, flow="cashout")
    return CASHOUT_AUTO_CHOOSE


async def cashout_auto_method_chosen(update, context):
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    if await handle_stale_flow_callback(
        update,
        context,
        flow="cashout",
        handler="cashout_auto_method_chosen",
        cleanup=_cleanup,
    ):
        return ConversationHandler.END
    await query.answer()
    data = query.data or ""
    if not data.startswith("coauto:"):
        return CASHOUT_AUTO_CHOOSE

    method_id = int(data.split(":")[1])
    method = get_method_by_id(method_id)
    if not method:
        return await _auto_escalate(
            update, context, detail="Selected method no longer available."
        )
    chat_id = query.message.chat.id if query.message else None
    if chat_id is not None and not is_cashout_method_allowed_for_chat(
        int(chat_id), method_id
    ):
        return await _auto_escalate(
            update, context, detail="Selected method not allowed for this group."
        )
    slug = (method.get("slug") or "").strip().lower()
    if not supported_cashout_slug(slug):
        return await _auto_escalate(
            update, context, detail=f"Method {slug!r} has no automated handle format."
        )

    context.chat_data["cashout_current_method"] = {
        "id": method_id,
        "name": method["name"],
        "slug": slug,
    }

    if method.get("has_sub_options"):
        subs = get_sub_options(method_id)
        if subs:
            buttons = []
            row = []
            for s in subs:
                row.append(
                    InlineKeyboardButton(
                        s["name"], callback_data=f"coautosub:{s['id']}"
                    )
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
            return CASHOUT_AUTO_SUB

    context.chat_data["cashout_payment_sub_option_id"] = None
    context.chat_data["cashout_method_display_name"] = method["name"]
    await _auto_prompt_handle(query, slug=slug, asset=None)
    return CASHOUT_AUTO_HANDLE


async def cashout_auto_sub_chosen(update, context):
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    if await handle_stale_flow_callback(
        update,
        context,
        flow="cashout",
        handler="cashout_auto_sub_chosen",
        cleanup=_cleanup,
    ):
        return ConversationHandler.END
    await query.answer()
    data = query.data or ""
    if not data.startswith("coautosub:"):
        return CASHOUT_AUTO_SUB

    sub_id = int(data.split(":")[1])
    sub = get_sub_option_by_id(sub_id)
    method = context.chat_data.get("cashout_current_method", {})
    if not sub:
        return await _auto_escalate(
            update, context, detail="Selected option no longer available."
        )
    context.chat_data["cashout_payment_sub_option_id"] = sub_id
    display = f"{method.get('name', '')} \u2014 {sub['name']}"
    context.chat_data["cashout_method_display_name"] = display
    await _auto_prompt_handle(query, slug=method.get("slug"), asset=sub["name"])
    return CASHOUT_AUTO_HANDLE


async def _auto_prompt_handle(query, *, slug, asset):
    prompt = _AUTO_HANDLE_PROMPTS.get(
        (slug or "").lower(),
        "Chips claimed! Please reply with your payout details.",
    )
    if "{asset}" in prompt:
        prompt = prompt.format(asset=asset or "crypto")
    try:
        await query.edit_message_text(prompt)
    except Exception:
        try:
            await query.message.chat.send_message(prompt)
        except Exception:
            pass


async def cashout_auto_handle_received(update, context):
    if not update.message:
        return CASHOUT_AUTO_HANDLE
    if is_update_too_old(update):
        log_stale_update(update, handler="cashout_auto_handle_received")
        return CASHOUT_AUTO_HANDLE

    sender_id = update.effective_user.id if update.effective_user else None
    target = _auto_target_id(context)
    # Only the target customer's messages count; helpers can quietly take over.
    if target is not None and sender_id != target:
        return CASHOUT_AUTO_HANDLE

    # Expected wizard input (the payout handle); suppress group_activity escalation.
    from bot.services.support_group_idle_episode import mark_expected_flow_input

    mark_expected_flow_input(context)

    method = context.chat_data.get("cashout_current_method", {})
    slug = method.get("slug")
    text = update.message.text or ""
    normalized = validate_cashout_handle(slug, text)
    if not normalized:
        return await _auto_escalate(
            update,
            context,
            detail=f"Player reply was not a valid {slug} handle: {text[:120]!r}",
        )
    # Handle is good — chips are only claimed now, at the very end, so the player
    # never waits on ClubGG before being asked for their payout details.
    context.chat_data["cashout_auto_payout_details"] = normalized
    return await _auto_run_claim(update, context)


async def _auto_finalize(update, context, *, payout_details):
    club_id = context.chat_data.get("cashout_club_id")
    chat_id = context.chat_data.get("cashout_chat_id")
    amount = context.chat_data.get("cashout_amount")
    method = context.chat_data.get("cashout_current_method", {})
    method_id = method.get("id")
    display = (
        context.chat_data.get("cashout_method_display_name")
        or method.get("name")
        or "Cashout"
    )
    sub_id = context.chat_data.get("cashout_payment_sub_option_id")
    initiated_by = (
        context.chat_data.get("cashout_user_id")
        or context.chat_data.get("cashout_admin_user_id")
        or 0
    )
    chat = update.effective_chat
    title = None
    try:
        if chat_id is not None:
            title, _cid = get_group_title_for_chat(int(chat_id))
    except Exception:
        title = None
    if not title and chat is not None:
        title = chat.title

    try:
        from cashier.services.auto_cashout import complete_auto_cashout

        ok, err = await complete_auto_cashout(
            club_id=int(club_id),
            chat_id=int(chat_id),
            group_title=title or "Unknown group",
            amount=amount,
            initiated_by=int(initiated_by),
            payment_method_id=int(method_id),
            payment_sub_option_id=sub_id,
            method_display_name=display,
            payout_details=payout_details,
        )
    except Exception:
        logger.exception("auto cashout: finalize crashed chat_id=%s", chat_id)
        return await _auto_escalate(
            update, context, detail="Recording the cashout crashed unexpectedly."
        )

    if not ok:
        return await _auto_escalate(
            update,
            context,
            detail=f"Recording the cashout failed: {err or 'unknown error'}",
        )

    if chat is not None:
        try:
            await chat.send_message(
                f"Your cashout of ${amount} via {display} is being processed. "
                f"You'll receive it shortly!"
            )
        except Exception:
            pass
    _cleanup_after_flow(context)
    return ConversationHandler.END


async def cashout_auto_offscript(update, context):
    if not context.chat_data.get("cashout_auto"):
        return ConversationHandler.END
    sender_id = update.effective_user.id if update.effective_user else None
    if _auto_sender_is_helper(context, sender_id):
        return None  # ignore; a human may be taking over
    target = _auto_target_id(context)
    if target is not None and sender_id != target:
        return None  # not the target customer; ignore
    # We own this escalation; stop group_activity from also firing player_idle.
    from bot.services.support_group_idle_episode import mark_expected_flow_input

    mark_expected_flow_input(context)
    text = ""
    if update.message:
        text = update.message.text or update.message.caption or "(non-text message)"
    return await _auto_escalate(
        update, context, detail=f"Off-script message: {text[:160]!r}"
    )


async def _show_method_keyboard(update, context, first_pick=False):
    """Show available methods. After the first pick (multi-mode), includes a Done button."""
    club_id = context.chat_data["cashout_club_id"]
    amount = context.chat_data["cashout_amount"]
    already_selected = {s["id"] for s in context.chat_data.get("cashout_selected", [])}
    is_multi = context.chat_data.get("cashout_multi", False)

    methods = get_methods_for_amount(club_id, "cashout", amount)
    chat = update.effective_chat
    if chat is not None:
        methods = filter_cashout_methods_for_chat(int(chat.id), methods)
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
            _cleanup_after_flow(context)
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
        sent = await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(buttons)
        )
        register_flow_callback_message(context, sent.message_id, flow="cashout")
    elif update.callback_query:
        sent = await update.callback_query.message.chat.send_message(
            text, reply_markup=InlineKeyboardMarkup(buttons)
        )
        register_flow_callback_message(context, sent.message_id, flow="cashout")
    return CASHOUT_CHOOSE


async def cashout_method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    if await handle_stale_flow_callback(
        update,
        context,
        flow="cashout",
        handler="cashout_method_chosen",
        cleanup=_cleanup,
    ):
        return ConversationHandler.END
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

    chat_id = query.message.chat.id if query.message else None
    if chat_id is not None and not is_cashout_method_allowed_for_chat(
        int(chat_id), method_id
    ):
        await query.edit_message_text(
            "That payment method is not available for this group."
        )
        return ConversationHandler.END

    context.chat_data["cashout_current_method"] = method

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

    amount = context.chat_data.get("cashout_amount", "?")
    tier = (
        get_tier_for_amount(method_id, amount) if isinstance(amount, Decimal) else None
    )
    if tier:
        response_data = pick_variant(method_id, tier_id=tier["id"])
        if not response_data:
            await query.edit_message_text(
                "This payment method is not configured yet. Please contact support."
            )
            return ConversationHandler.END
    else:
        response_data = pick_variant(method_id) or method
    await _send_response(query, response_data, amount, method["name"])
    context.chat_data.setdefault("cashout_selected", []).append(
        {"id": method_id, "name": method["name"]}
    )

    is_multi = context.chat_data.get("cashout_multi", False)
    if not is_multi:
        return await _finalize_cashout(update, context)

    return await _show_method_keyboard(update, context, first_pick=False)


async def cashout_sub_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return ConversationHandler.END
    query = update.callback_query
    if await handle_stale_flow_callback(
        update,
        context,
        flow="cashout",
        handler="cashout_sub_chosen",
        cleanup=_cleanup,
    ):
        return ConversationHandler.END
    await query.answer()

    data = query.data or ""
    if not data.startswith("cosub:"):
        return ConversationHandler.END

    sub_id = int(data.split(":")[1])
    sub = get_sub_option_by_id(sub_id)
    method = context.chat_data.get("cashout_current_method", {})
    if not sub:
        await query.edit_message_text("That option is no longer available.")
        _cleanup_after_flow(context)
        return ConversationHandler.END

    amount = context.chat_data.get("cashout_amount", "?")
    display = f"{method.get('name', '')} — {sub['name']}"
    await _send_response(query, sub, amount, display)

    context.chat_data.setdefault("cashout_selected", []).append(
        {"id": method.get("id"), "name": display}
    )

    is_multi = context.chat_data.get("cashout_multi", False)
    if not is_multi:
        return await _finalize_cashout(update, context)

    return await _show_method_keyboard(update, context, first_pick=False)


async def _send_response(query, data, amount, display_name):
    """Edit the keyboard message to the announcement, then send instructions as a new message."""
    announcement = f"Cashout request for ${amount} via {display_name}"
    await query.edit_message_text(announcement)
    await send_response_messages(query.message.chat, data)


async def _finalize_cashout(update, context):
    amount = context.chat_data.get("cashout_amount", "?")
    selected = context.chat_data.get("cashout_selected", [])
    club_id = context.chat_data.get("cashout_club_id")
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
    _cleanup_after_flow(context)
    return ConversationHandler.END


async def _send_simple_response(message, data):
    """Send the simple-mode response (text or photo) directly."""
    await send_response_messages(message, data)


def _record_cashout(context):
    club_id = context.chat_data.get("cashout_club_id")
    user_id = context.chat_data.get("cashout_user_id")
    chat_id = context.chat_data.get("cashout_chat_id")
    if club_id and user_id and chat_id:
        try:
            record_activity(club_id, user_id, chat_id, "cashout")
        except Exception:
            pass


def _cleanup(context):
    reset_flow_callback_messages(context, flow="cashout")
    clear_active_flow(context)
    for key in (
        "cashout_club_id",
        "cashout_chat_id",
        "cashout_user_id",
        "cashout_amount",
        "cashout_selected",
        "cashout_current_method",
        "cashout_multi",
        "cashout_simple_data",
        "cashout_admin_initiated",
        "cashout_admin_user_id",
        "cashout_auto",
        "cashout_auto_claim_key",
        "cashout_auto_claimed",
        "cashout_auto_payout_details",
        "cashout_union_shorthand",
        "cashout_method_display_name",
        "cashout_payment_sub_option_id",
    ):
        context.chat_data.pop(key, None)


def _cleanup_after_flow(context):
    chat_id = context.chat_data.get("cashout_chat_id")
    _cleanup(context)
    if chat_id is not None:
        try:
            from bot.services.support_group_idle_episode import close_episode

            close_episode(
                int(chat_id),
                job_queue=getattr(context, "job_queue", None),
            )
        except Exception:
            logger.debug(
                "cashout: close idle episode failed chat_id=%s",
                chat_id,
                exc_info=True,
            )
    popup_keyboard_svc.on_flow_exit_schedule_idle(context, chat_id)


async def cashout_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    club_id = context.chat_data.get("cashout_club_id")
    chat_id = context.chat_data.get("cashout_chat_id")
    if club_id and chat_id:
        try:
            cancel_last_cashout_activity(club_id, chat_id)
        except Exception:
            pass
    if update.message:
        await update.message.reply_text("Cashout cancelled.")
    _cleanup_after_flow(context)
    return ConversationHandler.END


async def cashout_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.chat_data.get("cashout_chat_id")
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
    _cleanup_after_flow(context)


TIMEOUT_SECONDS = 600

_CASHOUT_CANCEL = CommandHandler("cancel", cashout_cancel)


def get_cashout_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler(["cashout", "withdraw"], cashout_entry),
        ],
        states={
            CASHOUT_AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & AMOUNT_TEXT,
                    cashout_amount_received,
                ),
                _CASHOUT_CANCEL,
            ],
            CASHOUT_SIMPLE_AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & AMOUNT_TEXT,
                    cashout_simple_amount_received,
                ),
                _CASHOUT_CANCEL,
            ],
            CASHOUT_CHOOSE: [
                CallbackQueryHandler(cashout_method_chosen, pattern=r"^co:\d+$"),
                CallbackQueryHandler(cashout_method_chosen, pattern=r"^codone$"),
                _CASHOUT_CANCEL,
            ],
            CASHOUT_SUB: [
                CallbackQueryHandler(cashout_sub_chosen, pattern=r"^cosub:\d+$"),
                _CASHOUT_CANCEL,
            ],
            CASHOUT_AUTO_AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & AMOUNT_TEXT,
                    cashout_auto_amount_received,
                ),
                _CASHOUT_CANCEL,
                MessageHandler(~filters.COMMAND, cashout_auto_offscript),
            ],
            CASHOUT_AUTO_UNION: [
                CallbackQueryHandler(
                    cashout_auto_union_chosen, pattern=r"^coautounion:(RT|AT)$"
                ),
                _CASHOUT_CANCEL,
                MessageHandler(~filters.COMMAND, cashout_auto_offscript),
            ],
            CASHOUT_AUTO_CHOOSE: [
                CallbackQueryHandler(
                    cashout_auto_method_chosen, pattern=r"^coauto:\d+$"
                ),
                _CASHOUT_CANCEL,
                MessageHandler(~filters.COMMAND, cashout_auto_offscript),
            ],
            CASHOUT_AUTO_SUB: [
                CallbackQueryHandler(
                    cashout_auto_sub_chosen, pattern=r"^coautosub:\d+$"
                ),
                _CASHOUT_CANCEL,
                MessageHandler(~filters.COMMAND, cashout_auto_offscript),
            ],
            CASHOUT_AUTO_HANDLE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, cashout_auto_handle_received
                ),
                _CASHOUT_CANCEL,
                MessageHandler(
                    ~filters.COMMAND & ~filters.TEXT, cashout_auto_offscript
                ),
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, cashout_timeout),
            ],
        },
        fallbacks=[_CASHOUT_CANCEL],
        conversation_timeout=TIMEOUT_SECONDS,
        name="cashout_conv",
        per_chat=True,
        per_user=False,
    )
