"""Transfer conversation: move chips between the two unions of one club.

Player picks the destination club, then the amount. The bot claims from the other
union and adds to the chosen one. Anything off-script posts a single "an agent
will be with you shortly" and escalates, mirroring the automated cashout flow.
"""

from decimal import Decimal
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
from bot.services.chip_transfer import (
    build_transfer_plan,
    new_transfer_key,
    run_transfer,
    transfer_blocked_reason,
)
from bot.services.club import (
    get_club_allows_admin_commands,
    get_club_for_chat,
    get_group_title_for_chat,
    get_transfer_enabled,
    is_club_staff,
    set_aces_join_ack,
)
from bot.services.round_table_unions import (
    ACES_TABLE_SHORTHAND,
    deposit_unions_for_club,
    is_creator_club,
)
from bot.handlers.flow_cancel import (
    block_if_group_money_flow_active,
    clear_active_flow,
    mark_active_flow,
)
from bot.handlers.flow_staleness import (
    AMOUNT_TEXT,
    handle_stale_flow_callback,
    is_update_too_old,
    log_stale_update,
    parse_deposit_amount,
    register_flow_callback_message,
    reset_flow_callback_messages,
    transfer_amount_actor_allowed,
)
from bot.services import popup_keyboard as popup_keyboard_svc

logger = logging.getLogger(__name__)

TRANSFER_DEST, TRANSFER_AMOUNT = range(2)

TIMEOUT_SECONDS = 600

AGENT_SHORTLY_COPY = "An agent will be with you shortly."
DEST_PROMPT_COPY = "Which club would you like to transfer your chips to?"
AMOUNT_PROMPT_COPY = "How many chips would you like to transfer to {destination}?"


def _mark_expected(context):
    """Keep the idle watcher from escalating input this flow asked for."""
    try:
        from bot.services.support_group_idle_episode import mark_expected_flow_input

        mark_expected_flow_input(context)
    except Exception:
        logger.debug("transfer: mark_expected_flow_input failed", exc_info=True)


def _target_id(context):
    return context.chat_data.get("transfer_user_id")


def _sender_is_helper(context, sender_id):
    """True for global admins / club staff who may quietly take over (ignored)."""
    if sender_id is None:
        return True
    if sender_id in ADMIN_USER_IDS:
        return True
    club_id = context.chat_data.get("transfer_club_id")
    if club_id and is_club_staff(sender_id, club_id):
        return True
    return False


def _title_for_chat(chat):
    try:
        title, _cid = get_group_title_for_chat(int(chat.id))
    except Exception:
        title = None
    return title or chat.title


def _group_title(context, update=None):
    chat_id = context.chat_data.get("transfer_chat_id")
    title = None
    try:
        if chat_id is not None:
            title, _cid = get_group_title_for_chat(int(chat_id))
    except Exception:
        title = None
    if not title and update is not None and update.effective_chat is not None:
        title = update.effective_chat.title
    return title


def _current_plan(context):
    """Rebuild the plan from chat_data (plans are not stored across handlers)."""
    club_id = context.chat_data.get("transfer_club_id")
    chat_id = context.chat_data.get("transfer_chat_id")
    dest = context.chat_data.get("transfer_destination")
    if not club_id or chat_id is None or not dest:
        return None
    try:
        return build_transfer_plan(
            club_id=int(club_id), chat_id=int(chat_id), destination_shorthand=dest
        )
    except Exception:
        logger.exception("transfer: could not rebuild plan chat_id=%s", chat_id)
        return None


async def _escalate(update, context, *, detail, claimed_amount=None, plan=None):
    """Tell the player an agent is coming (once), Slack-escalate, and END."""
    club_id = context.chat_data.get("transfer_club_id")
    chat_id = context.chat_data.get("transfer_chat_id")
    chat = update.effective_chat
    if chat is not None:
        try:
            await chat.send_message(AGENT_SHORTLY_COPY)
        except Exception:
            pass
    try:
        from bot.services.escalation_notification import notify_transfer_escalation

        await notify_transfer_escalation(
            club_id=club_id,
            chat_id=int(chat_id) if chat_id is not None else 0,
            title=_group_title(context, update),
            detail=detail,
            claimed_amount=claimed_amount,
            source_club=plan.source_clubgg if plan else None,
            destination_club=plan.destination_clubgg if plan else None,
        )
    except Exception:
        logger.debug("transfer: escalation failed", exc_info=True)
    _cleanup_after_flow(context)
    return ConversationHandler.END


async def transfer_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not update.effective_chat or not update.effective_user:
        return ConversationHandler.END
    if update.message and is_update_too_old(update):
        log_stale_update(update, handler="transfer_entry")
        return ConversationHandler.END
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return ConversationHandler.END

    club_id = get_club_for_chat(chat.id)
    if not club_id:
        return ConversationHandler.END
    if not get_transfer_enabled(club_id):
        return ConversationHandler.END

    unions = deposit_unions_for_club(int(club_id))
    if not unions or len(unions) != 2:
        # Single-union clubs have nothing to transfer between.
        return ConversationHandler.END

    user_id = update.effective_user.id
    if user_id in ADMIN_USER_IDS and not get_club_allows_admin_commands(club_id):
        return ConversationHandler.END

    if await block_if_group_money_flow_active(
        update, context, starting="transfer", chat_id=chat.id
    ):
        return ConversationHandler.END

    _cleanup(context)
    # A bare /transfer is an expected request, never an escalation.
    _mark_expected(context)

    blocked = transfer_blocked_reason(int(club_id), _title_for_chat(chat))
    if blocked:
        logger.info(
            "transfer: unavailable chat_id=%s club_id=%s reason=%s",
            chat.id,
            club_id,
            blocked,
        )
        context.chat_data["transfer_club_id"] = club_id
        context.chat_data["transfer_chat_id"] = chat.id
        return await _escalate(
            update, context, detail=f"Transfer could not start: {blocked}."
        )

    context.chat_data["transfer_club_id"] = club_id
    context.chat_data["transfer_chat_id"] = chat.id
    if user_id in ADMIN_USER_IDS:
        context.chat_data["transfer_admin_initiated"] = True
        context.chat_data["transfer_admin_user_id"] = user_id
    else:
        context.chat_data["transfer_user_id"] = user_id
    mark_active_flow(context, "transfer")

    buttons = [
        [
            InlineKeyboardButton(
                union["label"], callback_data=f"trdest:{union['shorthand']}"
            )
        ]
        for union in unions
    ]
    sent = await message.reply_text(
        DEST_PROMPT_COPY, reply_markup=InlineKeyboardMarkup(buttons)
    )
    register_flow_callback_message(context, sent.message_id, flow="transfer")
    return TRANSFER_DEST


async def transfer_dest_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return TRANSFER_DEST
    await query.answer()
    if await handle_stale_flow_callback(
        update, context, flow="transfer", handler="transfer_dest_chosen",
        cleanup=_cleanup,
    ):
        return ConversationHandler.END

    club_id = context.chat_data.get("transfer_club_id")
    chat_id = context.chat_data.get("transfer_chat_id")
    if not club_id or chat_id is None:
        await query.edit_message_text("Transfer session expired. Use /transfer again.")
        _cleanup(context)
        return ConversationHandler.END

    shorthand = (query.data or "").split(":", 1)[-1].strip().upper()
    plan = build_transfer_plan(
        club_id=int(club_id), chat_id=int(chat_id), destination_shorthand=shorthand
    )
    if plan is None:
        await query.edit_message_text("That option is no longer available.")
        _cleanup(context)
        return ConversationHandler.END

    context.chat_data["transfer_destination"] = plan.destination_shorthand
    # In an admin-initiated flow the customer is identified by whoever sends the
    # amount, so only a player-run flow pins the target here.
    if (
        not context.chat_data.get("transfer_admin_initiated")
        and query.from_user
        and _target_id(context) is None
    ):
        context.chat_data["transfer_user_id"] = query.from_user.id

    await query.edit_message_text(
        AMOUNT_PROMPT_COPY.format(destination=plan.destination_clubgg)
    )
    return TRANSFER_AMOUNT


async def transfer_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return TRANSFER_AMOUNT
    if is_update_too_old(update):
        log_stale_update(update, handler="transfer_amount_received")
        _cleanup_after_flow(context)
        return ConversationHandler.END

    sender_id = update.effective_user.id if update.effective_user else None
    text = update.message.text or ""
    if not transfer_amount_actor_allowed(context, sender_id=sender_id, text=text):
        return TRANSFER_AMOUNT
    _mark_expected(context)

    if (
        context.chat_data.get("transfer_admin_initiated")
        and sender_id is not None
        and sender_id not in ADMIN_USER_IDS
    ):
        context.chat_data["transfer_user_id"] = sender_id

    amount = parse_deposit_amount(text)
    if amount is None:
        return TRANSFER_AMOUNT

    plan = _current_plan(context)
    if plan is None:
        return await _escalate(
            update, context, detail="Transfer destination could not be resolved."
        )

    context.chat_data["transfer_amount"] = amount
    return await _run(update, context, plan=plan, amount=amount)


def _format_chips(amount: Decimal) -> str:
    amt = amount.quantize(Decimal("0.01"))
    if amt == amt.to_integral_value():
        return f"{int(amt):,}"
    return f"{amt:,.2f}"


async def _run(update, context, *, plan, amount: Decimal):
    """Claim from the source union, then add to the destination union."""
    chat = update.effective_chat
    chips = _format_chips(amount)

    async def _say(text):
        if chat is None:
            return
        try:
            await chat.send_message(text)
        except Exception:
            pass

    await _say(
        f"Claiming {chips} chips from {plan.source_clubgg}"
        f"...this will just take a minute!"
    )

    key = context.chat_data.get("transfer_key")
    if not key:
        key = new_transfer_key(plan.chat_id)
        context.chat_data["transfer_key"] = key

    async def _on_claimed():
        await _say(f"Adding {chips} chips to {plan.destination_clubgg}...")

    result = await run_transfer(
        plan=plan,
        amount=amount,
        transfer_key=key,
        group_title=_group_title(context, update),
        on_claimed=_on_claimed,
    )

    if not result.ok:
        return await _escalate(
            update,
            context,
            detail=result.reason or f"Transfer failed on the {result.failed_leg} leg.",
            claimed_amount=result.claimed_amount,
            plan=plan,
        )

    _persist_aces_ack(context, plan)
    await _say(
        f"Successfully transferred {chips} chips from {plan.source_clubgg} "
        f"to {plan.destination_clubgg}!"
    )
    _cleanup_after_flow(context)
    return ConversationHandler.END


def _persist_aces_ack(context, plan) -> None:
    """A transfer into Aces means the player holds chips there.

    Recording the ack keeps /deposit and automated /cashout offering Aces, so the
    transferred chips cannot become unreachable through the bot.
    """
    if plan.destination_shorthand != ACES_TABLE_SHORTHAND:
        return
    try:
        if is_creator_club(int(plan.club_id)):
            set_aces_join_ack(int(plan.chat_id))
    except Exception:
        logger.warning(
            "transfer: failed to persist aces join ack chat_id=%s",
            plan.chat_id,
            exc_info=True,
        )


async def transfer_offscript(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Anything the flow did not ask for: escalate once and bow out."""
    if not context.chat_data.get("transfer_club_id"):
        return None
    sender_id = update.effective_user.id if update.effective_user else None
    if _sender_is_helper(context, sender_id):
        return None
    target = _target_id(context)
    if target is not None and sender_id != target:
        return None
    _mark_expected(context)
    text = (update.effective_message.text or "") if update.effective_message else ""
    return await _escalate(
        update,
        context,
        detail=f"Off-script message: {text[:160]!r}",
        plan=_current_plan(context),
    )


def _cleanup(context):
    reset_flow_callback_messages(context, flow="transfer")
    clear_active_flow(context)
    for key in (
        "transfer_club_id",
        "transfer_chat_id",
        "transfer_user_id",
        "transfer_admin_initiated",
        "transfer_admin_user_id",
        "transfer_destination",
        "transfer_amount",
        "transfer_key",
    ):
        context.chat_data.pop(key, None)


def _cleanup_after_flow(context):
    chat_id = context.chat_data.get("transfer_chat_id")
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
                "transfer: close idle episode failed chat_id=%s",
                chat_id,
                exc_info=True,
            )
    popup_keyboard_svc.on_flow_exit_schedule_idle(context, chat_id)


async def transfer_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("Transfer cancelled.")
    _cleanup_after_flow(context)
    return ConversationHandler.END


async def transfer_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.chat_data.get("transfer_chat_id")
    if chat_id:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "We didn't hear back from you so we are canceling your transfer! "
                    "Please use /transfer to try again!"
                ),
            )
        except Exception:
            pass
    _cleanup_after_flow(context)


_TRANSFER_CANCEL = CommandHandler("cancel", transfer_cancel)


def get_transfer_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("transfer", transfer_entry),
        ],
        states={
            TRANSFER_DEST: [
                CallbackQueryHandler(
                    transfer_dest_chosen, pattern=r"^trdest:(RT|AT|CC)$"
                ),
                _TRANSFER_CANCEL,
                MessageHandler(~filters.COMMAND, transfer_offscript),
            ],
            TRANSFER_AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & AMOUNT_TEXT,
                    transfer_amount_received,
                ),
                _TRANSFER_CANCEL,
                MessageHandler(~filters.COMMAND, transfer_offscript),
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, transfer_timeout),
            ],
        },
        fallbacks=[_TRANSFER_CANCEL],
        conversation_timeout=TIMEOUT_SECONDS,
        name="transfer_conv",
        per_chat=True,
        per_user=False,
    )
