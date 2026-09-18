"""Player /earlyrb: automated early feeback, with the canned request as fallback.

When the club has ``enable_auto_early_rakeback`` on and both external systems are
configured, this walks the player through club choice, a live fee lookup on the
ClubGG RPA bot, an Elevate quote, and a Claim button that records the payout and
adds the chips. Otherwise it behaves exactly as it always has: log the request,
ping an account manager, and tell the player someone will follow up.

Player-facing copy says **fee** and **feeback**, never rake or rakeback.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.handlers.flow_cancel import block_if_group_money_flow_active
from bot.handlers.flow_staleness import (
    handle_stale_flow_callback,
    register_flow_callback_message,
    reset_flow_callback_messages,
)
from bot.services.club import (
    check_earlyrb_recheck_throttle,
    get_auto_early_rakeback_enabled,
    get_club_by_id,
    get_club_for_chat,
    get_group_title_for_chat,
    record_activity,
    record_activity_for_chat,
    update_group_name,
)
from bot.services.round_table_unions import (
    cashout_unions_for_chat,
    union_label_for_shorthand,
    union_shorthands_for_club,
)

logger = logging.getLogger(__name__)

EARLYRB_ELIGIBLE_MESSAGE = (
    "We're checking your early rakeback now. Your account manager will follow up "
    "in this group shortly.\n\n"
    "Early feeback counts as a deposit and will reset the cashout timer"
)

EARLYRB_RECORD_FAILED_MESSAGE = (
    "We couldn't record your early rakeback request. Please try again in a minute "
    "or message your account manager."
)

UNION_PROMPT = "Which club would you like to claim your early feeback in?"
CANCELLED_COPY = "Early feeback cancelled."
TIMEOUT_COPY = "This early feeback request timed out. Send /earlyrb to start again."

EARLYRB_UNION, EARLYRB_CONFIRM = range(2)

TIMEOUT_SECONDS = 600

_CHAT_DATA_KEYS = (
    "earlyrb_club_id",
    "earlyrb_chat_id",
    "earlyrb_user_id",
    "earlyrb_title",
    "earlyrb_union_shorthand",
    "earlyrb_fee",
    "earlyrb_quote",
    "earlyrb_target",
    "earlyrb_player_id",
    "earlyrb_requoted",
)


def _cleanup(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in _CHAT_DATA_KEYS:
        context.chat_data.pop(key, None)
    reset_flow_callback_messages(context, flow="earlyrb")


def auto_earlyrb_available(club_id: int) -> bool:
    """Toggle on and both external systems configured on this worker."""
    from bot.services.clubgg_deposit_api import deposit_api_configured
    from bot.services.elevate_early_rakeback_api import early_rakeback_api_configured

    if not get_auto_early_rakeback_enabled(int(club_id)):
        return False
    if not deposit_api_configured():
        logger.info(
            "earlyrb: auto enabled for club %s but ClubGG API is not configured",
            club_id,
        )
        return False
    if not early_rakeback_api_configured():
        logger.info(
            "earlyrb: auto enabled for club %s but Elevate API is not configured",
            club_id,
        )
        return False
    return True


async def _canned_request(update: Update, club_id: int) -> None:
    """The pre-automation behaviour: record the request and hand off to an AM."""
    chat = update.effective_chat
    user = update.effective_user
    try:
        record_activity(club_id, user.id, chat.id, "earlyrb")
    except Exception:
        logger.exception(
            "earlyrb: record_activity failed club_id=%s chat_id=%s user_id=%s",
            club_id,
            chat.id,
            user.id,
        )
        await update.message.reply_text(EARLYRB_RECORD_FAILED_MESSAGE)
        return

    try:
        from bot.services.escalation_notification import notify_earlyrb_requested

        await notify_earlyrb_requested(
            club_id=club_id, chat_id=chat.id, title=chat.title
        )
    except Exception:
        logger.debug(
            "earlyrb: escalation notify failed chat_id=%s", chat.id, exc_info=True
        )

    await update.message.reply_text(EARLYRB_ELIGIBLE_MESSAGE)


async def earlyrb_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.effective_user:
        return ConversationHandler.END

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use /earlyrb in a club group.")
        return ConversationHandler.END

    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        await update.message.reply_text(
            "This group isn't linked to a club yet. The club owner must add the bot."
        )
        return ConversationHandler.END

    update_group_name(chat.id, chat.title)

    if not auto_earlyrb_available(club_id):
        await _canned_request(update, club_id)
        return ConversationHandler.END

    if await block_if_group_money_flow_active(
        update, context, starting="earlyrb", chat_id=chat.id
    ):
        return ConversationHandler.END

    allowed, throttle_msg = check_earlyrb_recheck_throttle(club_id, chat.id)
    if not allowed:
        await update.message.reply_text(throttle_msg)
        return ConversationHandler.END

    title = None
    try:
        title, _cid = get_group_title_for_chat(int(chat.id))
    except Exception:
        title = None
    title = title or chat.title

    context.chat_data["earlyrb_club_id"] = int(club_id)
    context.chat_data["earlyrb_chat_id"] = int(chat.id)
    context.chat_data["earlyrb_user_id"] = int(update.effective_user.id)
    context.chat_data["earlyrb_title"] = title

    unions = cashout_unions_for_chat(int(club_id), int(chat.id))
    if unions:
        buttons = [
            [
                InlineKeyboardButton(
                    u["label"], callback_data=f"ebunion:{u['shorthand']}"
                )
            ]
            for u in unions
        ]
        sent = await update.message.reply_text(
            UNION_PROMPT, reply_markup=InlineKeyboardMarkup(buttons)
        )
        register_flow_callback_message(context, sent.message_id, flow="earlyrb")
        return EARLYRB_UNION

    return await _run_lookup(update, context)


async def earlyrb_union_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    if await handle_stale_flow_callback(
        update,
        context,
        flow="earlyrb",
        handler="earlyrb_union_chosen",
        cleanup=_cleanup,
    ):
        return ConversationHandler.END
    await query.answer()

    data = query.data or ""
    shorthand = data.split(":", 1)[1].strip().upper() if ":" in data else ""
    club_id = context.chat_data.get("earlyrb_club_id")
    if not club_id or shorthand not in union_shorthands_for_club(int(club_id)):
        return EARLYRB_UNION

    context.chat_data["earlyrb_union_shorthand"] = shorthand
    label = union_label_for_shorthand(shorthand) or shorthand
    try:
        await query.edit_message_text(f"Early feeback in {label} requested.")
    except Exception:
        pass

    return await _run_lookup(update, context)


async def _run_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fee lookup then Elevate quote, ending in a Claim prompt or a plain answer."""
    from bot.services import early_rakeback_auto as auto

    chat = update.effective_chat
    club_id = int(context.chat_data["earlyrb_club_id"])
    chat_id = int(context.chat_data["earlyrb_chat_id"])
    user_id = context.chat_data.get("earlyrb_user_id")
    title = context.chat_data.get("earlyrb_title")
    union = context.chat_data.get("earlyrb_union_shorthand")

    # Anchors the re-check throttle: the screen robot is about to be busy for
    # this group whether or not anything is ultimately claimed.
    try:
        record_activity_for_chat(
            club_id, chat_id, "earlyrb_check", telegram_user_id=user_id
        )
    except Exception:
        logger.exception("earlyrb: could not record check activity chat_id=%s", chat_id)

    await chat.send_message(auto.FINDING_FEE_COPY)
    fee_stage = await auto.check_fee(
        club_id=club_id, chat_id=chat_id, group_title=title, union_shorthand=union
    )

    if fee_stage.kind == "escalate":
        await auto.escalate_fee_stage(
            club_id=club_id,
            chat_id=chat_id,
            group_title=title,
            detail=fee_stage.detail,
        )
        await chat.send_message(auto.ADMIN_SHORTLY_COPY)
        _cleanup(context)
        return ConversationHandler.END

    if fee_stage.kind == "no_fee":
        await chat.send_message(auto.NO_FEE_COPY)
        _cleanup(context)
        return ConversationHandler.END

    fee = fee_stage.fee
    club = get_club_by_id(club_id)
    target = auto.resolve_club_target(club.name if club else None, union)
    if target is None:
        await auto.escalate_fee_stage(
            club_id=club_id,
            chat_id=chat_id,
            group_title=title,
            detail=(
                f"could not map club {club.name if club else None!r} / union "
                f"{union!r} to a ClubGG club and Elevate slug"
            ),
        )
        await chat.send_message(auto.ADMIN_SHORTLY_COPY)
        _cleanup(context)
        return ConversationHandler.END

    await chat.send_message(auto.CALCULATING_COPY)
    quote_stage = await auto.quote_feeback(
        club_id=club_id,
        target=target,
        gg_player_id=fee.player_id,
        fee=fee,
        nickname=None,
    )

    if quote_stage.kind == "escalate":
        await auto.escalate_fee_stage(
            club_id=club_id,
            chat_id=chat_id,
            group_title=title,
            detail=quote_stage.detail,
        )
        await chat.send_message(auto.ADMIN_SHORTLY_COPY)
        _cleanup(context)
        return ConversationHandler.END

    if quote_stage.kind == "over_max":
        await auto.escalate_over_max(
            club_id=club_id,
            chat_id=chat_id,
            group_title=title,
            gg_player_id=fee.player_id,
            quote=quote_stage.quote,
            fee=fee,
        )
        await chat.send_message(quote_stage.player_message)
        _cleanup(context)
        return ConversationHandler.END

    if quote_stage.kind in ("nothing_remaining", "below_minimum"):
        await chat.send_message(quote_stage.player_message)
        _cleanup(context)
        return ConversationHandler.END

    context.chat_data["earlyrb_fee"] = fee
    context.chat_data["earlyrb_quote"] = quote_stage.quote
    context.chat_data["earlyrb_target"] = target
    context.chat_data["earlyrb_player_id"] = fee.player_id
    return await _prompt_claim(update, context, quote_stage.quote)


async def _prompt_claim(update: Update, context: ContextTypes.DEFAULT_TYPE, quote):
    from bot.services import early_rakeback_auto as auto

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Claim", callback_data="ebclaim"),
                InlineKeyboardButton("Cancel", callback_data="ebcancel"),
            ]
        ]
    )
    sent = await update.effective_chat.send_message(
        auto.format_claim_prompt(quote), reply_markup=markup
    )
    register_flow_callback_message(context, sent.message_id, flow="earlyrb")
    return EARLYRB_CONFIRM


def _is_requester(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Only the player who ran /earlyrb may claim (the conversation is per-chat)."""
    user = update.effective_user
    requester = context.chat_data.get("earlyrb_user_id")
    return user is not None and requester is not None and int(user.id) == int(requester)


async def earlyrb_claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.services import early_rakeback_auto as auto

    query = update.callback_query
    if not query:
        return ConversationHandler.END
    if await handle_stale_flow_callback(
        update, context, flow="earlyrb", handler="earlyrb_claim", cleanup=_cleanup
    ):
        return ConversationHandler.END
    if not _is_requester(update, context):
        await query.answer(
            "Only the player who ran /earlyrb can claim it.", show_alert=True
        )
        return EARLYRB_CONFIRM
    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    club_id = int(context.chat_data["earlyrb_club_id"])
    chat_id = int(context.chat_data["earlyrb_chat_id"])
    user_id = context.chat_data.get("earlyrb_user_id")
    title = context.chat_data.get("earlyrb_title")
    fee = context.chat_data["earlyrb_fee"]
    quote = context.chat_data["earlyrb_quote"]
    target = context.chat_data["earlyrb_target"]
    player_id = context.chat_data["earlyrb_player_id"]
    already_requoted = bool(context.chat_data.get("earlyrb_requoted"))

    idempotency_key = auto.new_idempotency_key(chat_id)
    claim_id = auto.create_claim_row(
        club_id=club_id,
        chat_id=chat_id,
        user_id=user_id,
        group_title=title,
        gg_player_id=player_id,
        nickname=quote.nickname,
        target=target,
        fee=fee,
        quote=quote,
        idempotency_key=idempotency_key,
    )

    stage = await auto.claim_feeback(
        club_id=club_id,
        chat_id=chat_id,
        user_id=user_id,
        group_title=title,
        gg_player_id=player_id,
        nickname=quote.nickname,
        target=target,
        fee=fee,
        quote=quote,
        idempotency_key=idempotency_key,
        claim_id=claim_id,
        allow_requote=not already_requoted,
    )

    if stage.kind == "amount_changed":
        context.chat_data["earlyrb_quote"] = stage.quote
        context.chat_data["earlyrb_requoted"] = True
        await update.effective_chat.send_message(
            "Your feeback changed while we were claiming it — here's the latest."
        )
        return await _prompt_claim(update, context, stage.quote)

    await update.effective_chat.send_message(stage.player_message)
    _cleanup(context)
    return ConversationHandler.END


async def earlyrb_decline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.services import early_rakeback_auto as auto

    query = update.callback_query
    if not query:
        return ConversationHandler.END
    if await handle_stale_flow_callback(
        update, context, flow="earlyrb", handler="earlyrb_decline", cleanup=_cleanup
    ):
        return ConversationHandler.END
    if not _is_requester(update, context):
        await query.answer(
            "Only the player who ran /earlyrb can cancel it.", show_alert=True
        )
        return EARLYRB_CONFIRM
    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await update.effective_chat.send_message(auto.CLAIM_CANCELLED_COPY)
    _cleanup(context)
    return ConversationHandler.END


async def earlyrb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _cleanup(context)
    if update.message:
        await update.message.reply_text(CANCELLED_COPY)
    return ConversationHandler.END


async def earlyrb_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    _cleanup(context)
    if chat is not None:
        try:
            await chat.send_message(TIMEOUT_COPY)
        except Exception:
            pass
    return ConversationHandler.END


_EARLYRB_CANCEL = CommandHandler("cancel", earlyrb_cancel)


def get_earlyrb_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("earlyrb", earlyrb_entry)],
        states={
            EARLYRB_UNION: [
                CallbackQueryHandler(
                    earlyrb_union_chosen, pattern=r"^ebunion:(RT|AT|CC)$"
                ),
                _EARLYRB_CANCEL,
            ],
            EARLYRB_CONFIRM: [
                CallbackQueryHandler(earlyrb_claim, pattern=r"^ebclaim$"),
                CallbackQueryHandler(earlyrb_decline, pattern=r"^ebcancel$"),
                _EARLYRB_CANCEL,
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, earlyrb_timeout),
            ],
        },
        fallbacks=[_EARLYRB_CANCEL],
        conversation_timeout=TIMEOUT_SECONDS,
        name="earlyrb_conv",
        per_chat=True,
        per_user=False,
    )
