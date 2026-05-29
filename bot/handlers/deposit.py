"""Deposit conversation: amount first, then filtered method selection, then optional crypto sub-option."""

import html
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
from bot.handlers.flow_cancel import clear_active_flow, mark_active_flow
from bot.handlers.response_utils import send_response_messages
from bot.services.stripe_deposit import (
    create_stripe_checkout_session,
    stripe_configured,
)
from db.connection import get_db
from db.models import Club

logger = logging.getLogger(__name__)

DEPOSIT_REFERRAL, DEPOSIT_AMOUNT, DEPOSIT_UNION, DEPOSIT_CHOOSE, DEPOSIT_SUB = range(5)

_CHECKOUT_SETTING_KEYS = (
    "use_group_checkout_link",
    "group_checkout_provider",
    "hyperlink_text",
    "min_amount",
    "max_amount",
)


def _apply_checkout_layer(target: dict, source: dict | None) -> None:
    """Overlay checkout fields from source onto target (method → tier → variant)."""
    if not source:
        return
    if source.get("use_group_checkout_link") is not None:
        enabled = bool(source["use_group_checkout_link"])
        target["use_group_checkout_link"] = enabled
        if not enabled:
            target.pop("group_checkout_provider", None)
    if source.get("group_checkout_provider"):
        target["group_checkout_provider"] = source.get("group_checkout_provider")
    elif target.get("use_group_checkout_link") and not target.get("group_checkout_provider"):
        target["group_checkout_provider"] = "stripe"
    if source.get("hyperlink_text"):
        target["hyperlink_text"] = source.get("hyperlink_text")
    if source.get("min_amount") is not None:
        target["min_amount"] = source.get("min_amount")
    if source.get("max_amount") is not None:
        target["max_amount"] = source.get("max_amount")


def _with_method_checkout_settings(
    response_data: dict,
    method: dict | None,
    *,
    tier: dict | None = None,
) -> dict:
    """Merge checkout settings: method → tier → variant (response_data)."""
    merged = dict(response_data)
    checkout: dict = {}
    for layer in (method, tier, response_data):
        _apply_checkout_layer(checkout, layer)
    merged.update(checkout)
    return merged


# Temporary until dashboard tier/variant Stripe flags are fully migrated.
_STRIPE_HARDCODE_SLUGS = frozenset({"cashapp", "applepay"})
_STRIPE_HARDCODE_MAX = Decimal("100")


def _apply_hardcoded_stripe_below_100(
    response_data: dict,
    *,
    method_slug: str,
    amount,
) -> dict:
    """Force Stripe checkout for Cashapp / Apple Pay deposits up to $100."""
    slug = (method_slug or "").strip().lower()
    if slug not in _STRIPE_HARDCODE_SLUGS:
        return response_data
    if not isinstance(amount, Decimal) or amount > _STRIPE_HARDCODE_MAX:
        return response_data
    merged = dict(response_data)
    merged["use_group_checkout_link"] = True
    merged["group_checkout_provider"] = "stripe"
    merged.setdefault("hyperlink_text", "PAY HERE")
    return merged


def _stripe_checkout_enabled(response_data: dict) -> bool:
    if not response_data.get("use_group_checkout_link"):
        return False
    provider = (response_data.get("group_checkout_provider") or "stripe").strip().lower()
    return provider == "stripe"


def _response_has_hyperlink_placeholder(response_data: dict) -> bool:
    for field in ("response_text", "response_caption"):
        if "{{hyperlink}}" in (response_data.get(field) or ""):
            return True
    return False


def _build_stripe_response_payload(response_data: dict, checkout_url: str) -> dict:
    hyperlink_text = (response_data.get("hyperlink_text") or "PAY HERE").strip() or "PAY HERE"
    safe_url = html.escape(checkout_url, quote=True)
    safe_label = html.escape(hyperlink_text, quote=False)
    html_link = f'<a href="{safe_url}">{safe_label}</a>'

    payload = dict(response_data)
    rtype = payload.get("response_type") or "text"
    if rtype == "text":
        text_field = "response_text"
        template = (payload.get("response_text") or "").strip()
    else:
        text_field = "response_caption"
        template = (payload.get("response_caption") or "").strip()

    if "{{hyperlink}}" in template:
        html_text = template.replace("{{hyperlink}}", html_link)
        plain_text = template.replace("{{hyperlink}}", checkout_url)
        payload[text_field] = html_text
        payload["parse_mode"] = "HTML"
        payload["disable_web_page_preview"] = True
        payload["_stripe_plain_fallback"] = plain_text
        payload["_stripe_plain_field"] = text_field
    elif template:
        payload[text_field] = template
        payload["_stripe_link_only_html"] = html_link
        payload["_stripe_link_only_plain"] = checkout_url
    else:
        payload[text_field] = html_link
        payload["parse_mode"] = "HTML"
        payload["disable_web_page_preview"] = True
        payload["_stripe_plain_fallback"] = checkout_url
        payload["_stripe_plain_field"] = text_field
    return payload


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

        mark_active_flow(context, "deposit")
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
        mark_active_flow(context, "deposit")
        await update.message.reply_text(
            "Welcome to the club! How did you hear about us? "
            "If it was a player, please type their GG username."
        )
        return DEPOSIT_REFERRAL

    if simple:
        return await _finish_simple_deposit(update.message, context)

    mark_active_flow(context, "deposit")
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

    method_slug = (method.get("slug") or "").strip().lower()
    logger.info(
        "deposit_method_chosen chat_id=%s method_id=%s slug=%r name=%r has_sub_options=%s stripe_configured=%s",
        query.message.chat.id if query.message else None,
        method_id,
        method_slug,
        method.get("name"),
        method.get("has_sub_options"),
        stripe_configured(),
    )

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
        response_data = _with_method_checkout_settings(response_data, method, tier=tier)
    else:
        response_data = pick_variant(method_id) or method
        response_data = _with_method_checkout_settings(response_data, method)
    ok = await _send_deposit_method_response(
        query,
        context,
        amount=amount,
        display_name=method["name"],
        method_id=method_id,
        method_slug=method_slug,
        response_data=response_data,
    )
    if not ok:
        return ConversationHandler.END
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
    parent = get_method_by_id(int(parent_method_id)) if parent_method_id else None
    parent_slug = (parent.get("slug") or "") if parent else ""
    tier = (
        get_tier_for_amount(int(parent_method_id), amount)
        if parent_method_id and isinstance(amount, Decimal)
        else None
    )
    response_data = _with_method_checkout_settings(sub, parent, tier=tier)
    sub_slug = (sub.get("slug") or "").strip().lower()
    logger.info(
        "deposit_sub_chosen chat_id=%s sub_id=%s sub_slug=%r parent_slug=%r group_checkout=%s",
        query.message.chat.id if query.message else None,
        sub_id,
        sub_slug,
        parent_slug,
        _stripe_checkout_enabled(response_data),
    )

    ok = await _send_deposit_method_response(
        query,
        context,
        amount=amount,
        display_name=display,
        method_id=int(parent_method_id) if parent_method_id else None,
        method_slug=parent_slug or sub_slug,
        response_data=response_data,
    )
    if not ok:
        return ConversationHandler.END
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
) -> bool:
    """When dashboard group Stripe checkout is enabled, create a per-group link from response text.

    Returns True if the deposit response was delivered, False on hard failure.
    """
    response_data = _apply_hardcoded_stripe_below_100(
        response_data,
        method_slug=method_slug,
        amount=amount,
    )
    slug = (method_slug or "").strip().lower()
    use_stripe_checkout = _stripe_checkout_enabled(response_data)
    if (
        not use_stripe_checkout
        and _response_has_hyperlink_placeholder(response_data)
        and response_data.get("use_group_checkout_link")
    ):
        response_data = {
            **response_data,
            "group_checkout_provider": response_data.get("group_checkout_provider") or "stripe",
        }
        use_stripe_checkout = _stripe_checkout_enabled(response_data)
    chat_id = context.chat_data.get("deposit_chat_id") or (
        query.message.chat.id if query.message else None
    )
    club_id = context.chat_data.get("deposit_club_id")

    logger.info(
        "deposit_send_response chat_id=%s club_id=%s method_id=%s slug=%r group_checkout=%s configured=%s",
        chat_id,
        club_id,
        method_id,
        slug,
        use_stripe_checkout,
        stripe_configured(),
    )

    if use_stripe_checkout and not stripe_configured():
        logger.warning("deposit: stripe checkout requested but STRIPE_SECRET_KEY not set chat_id=%s", chat_id)
        if club_id is not None:
            await _notify_missing_stripe_secret(context, int(club_id))
        await query.edit_message_text(
            "Card checkout is not available right now (Stripe not configured on the bot). "
            "Please contact support."
        )
        return False

    if use_stripe_checkout and stripe_configured():
        if club_id is None:
            logger.error("deposit: stripe checkout requested but deposit_club_id missing chat_id=%s", chat_id)
            await query.edit_message_text(
                "This group is not linked to a club. Card checkout cannot be started."
            )
            return False

        try:
            group_title = getattr(query.message.chat, "title", None)
            result = create_stripe_checkout_session(
                telegram_chat_id=int(chat_id),
                club_id=int(club_id),
                payment_method_id=method_id,
                group_title=group_title,
                checkout_min_usd=response_data.get("min_amount"),
                checkout_max_usd=response_data.get("max_amount"),
            )
            await query.edit_message_text(f"Deposit via {display_name}")

            payload = _build_stripe_response_payload(response_data, result.checkout_url)
            plain_field = payload.pop("_stripe_plain_field", "response_text")
            plain_fallback = payload.pop("_stripe_plain_fallback", None)
            link_only_html = payload.pop("_stripe_link_only_html", None)
            link_only_plain = payload.pop("_stripe_link_only_plain", None)
            try:
                await send_response_messages(query.message.chat, payload)
                if link_only_html:
                    await query.message.chat.send_message(
                        link_only_html,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
            except Exception:
                logger.warning(
                    "deposit: HTML templated response failed, retrying plain link chat_id=%s",
                    chat_id,
                    exc_info=True,
                )
                fallback = dict(response_data)
                if plain_fallback is not None:
                    fallback[plain_field] = plain_fallback
                    fallback["parse_mode"] = None
                    fallback["disable_web_page_preview"] = True
                    await send_response_messages(query.message.chat, fallback)
                elif link_only_plain:
                    await send_response_messages(query.message.chat, payload)
                    await query.message.chat.send_message(link_only_plain)
            logger.info(
                "deposit: stripe checkout sent chat_id=%s session_id=%s customer_id=%s",
                chat_id,
                result.session_id,
                result.customer_id,
            )
            return True
        except Exception as e:
            err_detail = _stripe_error_detail(e)
            logger.exception(
                "deposit: stripe checkout failed chat_id=%s club_id=%s method_id=%s slug=%r "
                "amount=%r checkout_min_usd=%r checkout_max_usd=%r display_name=%r: %s",
                chat_id,
                club_id,
                method_id,
                slug,
                amount,
                response_data.get("min_amount"),
                response_data.get("max_amount"),
                display_name,
                err_detail,
            )
            if club_id is not None:
                await _notify_stripe_checkout_failure(
                    context,
                    club_id=int(club_id),
                    chat_id=int(chat_id) if chat_id is not None else None,
                    group_title=group_title,
                    method_id=method_id,
                    method_slug=slug,
                    display_name=display_name,
                    amount=amount,
                    checkout_min_usd=response_data.get("min_amount"),
                    checkout_max_usd=response_data.get("max_amount"),
                    error_detail=err_detail,
                )
            await query.edit_message_text(
                "Card checkout failed to start. Please try again in a minute or contact support."
            )
            return False

    if _response_has_hyperlink_placeholder(response_data):
        logger.warning(
            "deposit: {{hyperlink}} in response but Stripe checkout not used "
            "chat_id=%s method_id=%s group_checkout=%s configured=%s",
            chat_id,
            method_id,
            response_data.get("use_group_checkout_link"),
            stripe_configured(),
        )
        if stripe_configured() and club_id is not None:
            await query.edit_message_text(
                "Checkout link could not be generated. Enable Use group specific link "
                "on this tier in the dashboard, set STRIPE_SECRET_KEY on the worker, "
                "and restart the bot."
            )
            return False

    await _send_response(query, response_data, amount, display_name)
    return True


def _stripe_error_detail(exc: BaseException) -> str:
    """Human-readable Stripe/API error for logs and admin DM."""
    parts: list[str] = [type(exc).__name__]
    msg = str(exc).strip()
    if msg:
        parts.append(msg[:300])
    code = getattr(exc, "code", None)
    if code:
        parts.append(f"code={code}")
    user_msg = getattr(exc, "user_message", None)
    if user_msg:
        um = str(user_msg).strip()
        if um and um not in msg:
            parts.append(f"stripe_message={um[:200]}")
    return " | ".join(parts)[:500]


async def _notify_stripe_checkout_failure(
    context,
    *,
    club_id: int,
    chat_id: int | None,
    group_title: str | None,
    method_id: int | None,
    method_slug: str,
    display_name: str,
    amount,
    checkout_min_usd,
    checkout_max_usd,
    error_detail: str,
) -> None:
    """DM the club owner when Stripe Checkout session creation fails."""
    try:
        with get_db() as session:
            club = session.query(Club).filter(Club.id == int(club_id)).one_or_none()
            if not club:
                logger.warning("deposit: stripe failure notify skipped — club_id=%s not found", club_id)
                return
            admin_id = int(club.telegram_user_id)
            club_name = club.name
    except Exception:
        logger.warning("deposit: stripe failure notify — could not load club_id=%s", club_id, exc_info=True)
        return

    lines = [
        f"[{club_name}] Stripe checkout failed to start",
        "",
    ]
    if group_title:
        lines.append(f"Group: {group_title[:120]}")
    if chat_id is not None:
        lines.append(f"Chat id: {chat_id}")
    if display_name or method_slug:
        lines.append(f"Method: {display_name or method_slug} ({method_slug or 'n/a'})")
    if method_id is not None:
        lines.append(f"Method id: {method_id}")
    if amount is not None and amount != "?":
        lines.append(f"Deposit amount entered: ${amount}")
    if checkout_min_usd is not None or checkout_max_usd is not None:
        lo = f"${checkout_min_usd}" if checkout_min_usd is not None else "—"
        hi = f"${checkout_max_usd}" if checkout_max_usd is not None else "—"
        lines.append(f"Checkout limits (dashboard): {lo} – {hi}")
    lines.extend(["", f"Error: {error_detail}", "", "See worker logs for full traceback."])
    text = "\n".join(lines)[:3900]

    try:
        await context.bot.send_message(chat_id=admin_id, text=text)
        logger.info(
            "deposit: stripe failure DM sent club_id=%s admin_user_id=%s chat_id=%s",
            club_id,
            admin_id,
            chat_id,
        )
    except Exception:
        logger.warning(
            "deposit: stripe failure DM failed club_id=%s admin_user_id=%s (did they /start the bot?)",
            club_id,
            admin_id,
            exc_info=True,
        )


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
    clear_active_flow(context)
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

_DEPOSIT_CANCEL = CommandHandler("cancel", deposit_cancel)


def get_deposit_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("deposit", deposit_entry)],
        states={
            DEPOSIT_REFERRAL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, deposit_referral_received
                ),
                _DEPOSIT_CANCEL,
            ],
            DEPOSIT_AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, deposit_amount_received
                ),
                _DEPOSIT_CANCEL,
            ],
            DEPOSIT_UNION: [
                CallbackQueryHandler(
                    deposit_union_chosen, pattern=r"^depunion:(RT|AT)$"
                ),
                _DEPOSIT_CANCEL,
            ],
            DEPOSIT_CHOOSE: [
                CallbackQueryHandler(deposit_method_chosen, pattern=r"^dep:\d+$"),
                _DEPOSIT_CANCEL,
            ],
            DEPOSIT_SUB: [
                CallbackQueryHandler(deposit_sub_chosen, pattern=r"^depsub:\d+$"),
                _DEPOSIT_CANCEL,
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, deposit_timeout),
            ],
        },
        fallbacks=[_DEPOSIT_CANCEL],
        conversation_timeout=TIMEOUT_SECONDS,
        name="deposit_conv",
        per_chat=True,
        per_user=False,
    )
