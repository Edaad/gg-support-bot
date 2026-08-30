"""Global /cancel and DM flow mutual exclusion for staff wizards."""

from __future__ import annotations

from typing import Literal

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

FlowName = Literal[
    "deposit",
    "cashout",
    "transfer",
    "bonus",
    "issue_report",
    "inactive_outreach_send",
    "support_note",
    "deposit_access",
    "cashout_access",
]

DmFlowName = Literal[
    "bonus",
    "issue_report",
    "inactive_outreach_send",
    "support_note",
    "deposit_access",
    "cashout_access",
]

ACTIVE_FLOW_KEY = "active_bot_flow"

_DM_FLOW_COMMANDS: dict[DmFlowName, str] = {
    "bonus": "/bonus",
    "issue_report": "/report",
    "inactive_outreach_send": "/sendinactive",
    "support_note": "/note",
    "deposit_access": "/depositaccess",
    "cashout_access": "/cashoutaccess",
}

_DEPOSIT_ACTIVE_KEYS = (
    "deposit_club_id",
    "deposit_amount",
    "deposit_method_id",
    "deposit_admin_initiated",
    "deposit_simple_data",
)
_CASHOUT_ACTIVE_KEYS = (
    "cashout_club_id",
    "cashout_amount",
    "cashout_method_id",
    "cashout_admin_initiated",
    "cashout_simple_data",
)
_TRANSFER_ACTIVE_KEYS = (
    "transfer_club_id",
    "transfer_amount",
    "transfer_destination",
    "transfer_admin_initiated",
)
_BONUS_ACTIVE_KEYS = (
    "bonus_admin_id",
    "bonus_draft_id",
    "bonus_group_title",
    "bonus_gg_player_id",
    "bonus_amount",
    "bonus_metadata",
)
_ISSUE_REPORT_WIZARD_KEYS = (
    "ir_draft_id",
    "ir_club_id",
    "ir_group_title",
    "ir_telegram_chat_id",
    "ir_notify_tags",
    "ir_title",
    "ir_details",
    "ir_evidence",
    "ir_admin_id",
)
_INACTIVE_OUTREACH_SEND_KEYS = (
    "io_club_key",
    "io_message",
    "io_admin_id",
)
_SUPPORT_NOTE_ACTIVE_KEYS = (
    "support_note_admin_id",
    "support_note_club_id",
    "support_note_gg_player_id",
    "support_note_situation",
    "support_note_actions",
    "support_note_source_chat_id",
)
_DEPOSIT_ACCESS_ACTIVE_KEYS = (
    "deposit_access_step",
    "deposit_access_admin_id",
    "deposit_access_chat_id",
    "deposit_access_club_id",
    "deposit_access_group_title",
    "deposit_access_action",
    "deposit_access_method_id",
)
_CASHOUT_ACCESS_ACTIVE_KEYS = (
    "cashout_access_step",
    "cashout_access_admin_id",
    "cashout_access_chat_id",
    "cashout_access_club_id",
    "cashout_access_group_title",
    "cashout_access_action",
    "cashout_access_method_id",
)

_DM_FLOW_CHECKERS: tuple[tuple[DmFlowName, str], ...] = (
    ("bonus", "bonus_flow_active"),
    ("issue_report", "issue_report_flow_active"),
    ("inactive_outreach_send", "inactive_outreach_send_flow_active"),
    ("support_note", "support_note_flow_active"),
    ("deposit_access", "deposit_access_flow_active"),
    ("cashout_access", "cashout_access_flow_active"),
)


def mark_active_flow(context: ContextTypes.DEFAULT_TYPE, flow: FlowName) -> None:
    context.user_data[ACTIVE_FLOW_KEY] = flow


def clear_active_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(ACTIVE_FLOW_KEY, None)


def deposit_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return any(k in context.chat_data for k in _DEPOSIT_ACTIVE_KEYS)


def cashout_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return any(k in context.chat_data for k in _CASHOUT_ACTIVE_KEYS)


def transfer_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return any(k in context.chat_data for k in _TRANSFER_ACTIVE_KEYS)


def deposit_payment_wait_active(chat_id: int | None) -> bool:
    """True while post-instructions deposit chase is still open for this group."""
    if chat_id is None:
        return False
    from bot.services import group_activity as ga

    return ga.deposit_instructions_pending(int(chat_id))


def deposit_blocking_active(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int | None = None,
) -> bool:
    """Wizard still open, or payment instructions wait not yet cleared."""
    if deposit_flow_active(context):
        return True
    cid = chat_id
    if cid is None:
        cid = context.chat_data.get("deposit_chat_id")
    return deposit_payment_wait_active(cid)


def cashout_blocking_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return cashout_flow_active(context)


GroupMoneyFlow = Literal["deposit", "cashout", "transfer"]

_GROUP_MONEY_FLOW_COMMANDS: dict[GroupMoneyFlow, str] = {
    "deposit": "/deposit",
    "cashout": "/cashout",
    "transfer": "/transfer",
}


def format_group_flow_block_message(*, active: GroupMoneyFlow) -> str:
    cmd = _GROUP_MONEY_FLOW_COMMANDS.get(active, "/deposit")
    return (
        f"You already have an active {cmd} in progress. "
        "Send /cancel to abort it first."
    )


async def block_if_group_money_flow_active(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    starting: GroupMoneyFlow,
    chat_id: int | None = None,
) -> bool:
    """Block starting a money flow when another (or the same) is still open.

    Returns True when entry must abort (a reply was sent).
    """
    cid = chat_id
    if cid is None and update.effective_chat is not None:
        cid = update.effective_chat.id

    open_flows: dict[GroupMoneyFlow, bool] = {
        "deposit": deposit_blocking_active(context, cid),
        "cashout": cashout_blocking_active(context),
        "transfer": transfer_flow_active(context),
    }
    # Report a different open flow first; only fall back to naming the same one.
    others = [f for f, is_open in open_flows.items() if is_open and f != starting]
    if others:
        active: GroupMoneyFlow = others[0]
    elif open_flows[starting]:
        active = starting
    else:
        return False

    message = format_group_flow_block_message(active=active)
    reply = update.effective_message
    if reply is not None:
        await reply.reply_text(message)
    return True


async def clear_deposit_payment_wait(
    chat_id: int | None,
    *,
    job_queue=None,
) -> bool:
    """Clear sticky post-instructions deposit wait. Returns True if it was active."""
    if chat_id is None:
        return False
    from bot.services import escalation_notification as esc
    from bot.services import group_activity as ga

    if not ga.deposit_instructions_pending(int(chat_id)):
        return False
    esc.cancel_deposit_sent_watch(int(chat_id), job_queue=job_queue)
    return True


def bonus_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    if context.user_data.get("bonus_step"):
        return True
    return any(k in context.user_data for k in _BONUS_ACTIVE_KEYS)


def issue_report_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    if context.user_data.get(ACTIVE_FLOW_KEY) == "issue_report":
        return True
    return any(k in context.user_data for k in _ISSUE_REPORT_WIZARD_KEYS)


def inactive_outreach_send_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    from bot.handlers.inactive_outreach_send import sendinactive_flow_active

    if sendinactive_flow_active(context):
        return True
    return any(k in context.user_data for k in _INACTIVE_OUTREACH_SEND_KEYS)


def support_note_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    if context.user_data.get(ACTIVE_FLOW_KEY) == "support_note":
        return True
    return any(k in context.user_data for k in _SUPPORT_NOTE_ACTIVE_KEYS)


def deposit_access_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    from bot.handlers.deposit_access import deposit_access_flow_active as _active

    if _active(context):
        return True
    return any(k in context.user_data for k in _DEPOSIT_ACCESS_ACTIVE_KEYS)


def cashout_access_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    from bot.handlers.deposit_access import cashout_access_flow_active as _active

    if _active(context):
        return True
    return any(k in context.user_data for k in _CASHOUT_ACCESS_ACTIVE_KEYS)


def _dm_flow_is_active(context: ContextTypes.DEFAULT_TYPE, flow: DmFlowName) -> bool:
    if flow == "bonus":
        return bonus_flow_active(context)
    if flow == "issue_report":
        return issue_report_flow_active(context)
    if flow == "inactive_outreach_send":
        return inactive_outreach_send_flow_active(context)
    if flow == "support_note":
        return support_note_flow_active(context)
    if flow == "deposit_access":
        return deposit_access_flow_active(context)
    if flow == "cashout_access":
        return cashout_access_flow_active(context)
    return False


def get_active_dm_flow(context: ContextTypes.DEFAULT_TYPE) -> DmFlowName | None:
    """Return the active DM staff flow, preferring the most recently marked flow."""
    latest = context.user_data.get(ACTIVE_FLOW_KEY)
    if latest in _DM_FLOW_COMMANDS and _dm_flow_is_active(context, latest):
        return latest
    for flow, _ in _DM_FLOW_CHECKERS:
        if flow != latest and _dm_flow_is_active(context, flow):
            return flow
    return None


def format_active_flow_block_message(
    active: DmFlowName,
    *,
    starting: DmFlowName,
) -> str:
    active_cmd = _DM_FLOW_COMMANDS[active]
    starting_cmd = _DM_FLOW_COMMANDS[starting]
    return (
        f"Finish or /cancel your active {active_cmd} flow "
        f"before starting {starting_cmd}."
    )


def format_active_flow_same_flow_message(flow: DmFlowName) -> str:
    cmd = _DM_FLOW_COMMANDS[flow]
    return f"You already have an active {cmd} in progress. Send /cancel to abort it first."


async def block_if_dm_flow_active(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    starting: DmFlowName,
) -> bool:
    """Return True when entry must abort (a reply was sent)."""
    active = get_active_dm_flow(context)
    if active is None:
        return False

    if active == starting:
        message = format_active_flow_same_flow_message(starting)
    else:
        message = format_active_flow_block_message(active, starting=starting)

    if update.message:
        await update.message.reply_text(message)
    elif update.callback_query:
        await update.callback_query.edit_message_text(message)
    return True


async def cancel_active_dm_flow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    flow: DmFlowName,
) -> bool:
    """Cancel a DM staff flow. Returns True when a flow was cancelled."""
    if not _dm_flow_is_active(context, flow):
        return False

    if flow == "inactive_outreach_send":
        from bot.handlers.inactive_outreach_send import sendinactive_cancel

        await sendinactive_cancel(update, context)
        return True

    if flow == "issue_report":
        from bot.handlers import issue_reports as ir_mod
        from bot.handlers.issue_reports import issue_report_cancel

        await issue_report_cancel(update, context)
        if ir_mod._report_conversation is not None:
            ir_mod.sync_report_conv_state(
                ir_mod._report_conversation,
                update,
                ConversationHandler.END,
            )
        return True

    if flow == "bonus":
        from bot.handlers.bonus import bonus_cancel

        await bonus_cancel(update, context)
        return True

    if flow == "support_note":
        from bot.handlers.support_notes import support_note_cancel

        await support_note_cancel(update, context)
        return True

    if flow == "deposit_access":
        from bot.handlers.deposit_access import depositaccess_cancel

        await depositaccess_cancel(update, context)
        return True

    if flow == "cashout_access":
        from bot.handlers.deposit_access import cashoutaccess_cancel

        await cashoutaccess_cancel(update, context)
        return True

    return False


def _cancel_order(context: ContextTypes.DEFAULT_TYPE) -> list[FlowName]:
    """Prefer the flow the user started most recently, then other active flows."""
    latest = context.user_data.get(ACTIVE_FLOW_KEY)
    order: list[FlowName] = []
    if latest in (
        "deposit",
        "cashout",
        "bonus",
        "issue_report",
        "inactive_outreach_send",
        "support_note",
        "deposit_access",
        "cashout_access",
    ):
        order.append(latest)
    for name in (
        "bonus",
        "issue_report",
        "inactive_outreach_send",
        "support_note",
        "deposit_access",
        "cashout_access",
        "deposit",
        "cashout",
    ):
        if name not in order:
            order.append(name)
    return order


async def dm_flow_cancel_priority(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Group -1 /cancel: abort DM flows even when ConversationHandler state is stale.

    Only runs in private chats — group /cancel must not kill a DM /report, etc.
    """
    from telegram.ext import ApplicationHandlerStop

    if not update.message:
        return
    chat = update.effective_chat
    if chat is None or getattr(chat, "type", None) != "private":
        return

    active = get_active_dm_flow(context)
    if active is None:
        return

    if await cancel_active_dm_flow(update, context, flow=active):
        raise ApplicationHandlerStop()


async def flow_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel deposit/cashout in this chat, or DM staff flows only in private."""
    if not update.message:
        return

    chat = update.effective_chat
    is_private = chat is not None and getattr(chat, "type", None) == "private"

    if is_private:
        active_dm = get_active_dm_flow(context)
        if active_dm is not None:
            await cancel_active_dm_flow(update, context, flow=active_dm)
            return

    for flow in _cancel_order(context):
        if flow == "deposit" and deposit_flow_active(context):
            from bot.handlers.deposit import deposit_cancel

            await deposit_cancel(update, context)
            return
        if flow == "cashout" and cashout_flow_active(context):
            from bot.handlers.cashout import cashout_cancel

            await cashout_cancel(update, context)
            return

    # Wizard already ended, but payment-wait flag still blocks /cashout|/deposit.
    if not is_private and chat is not None:
        cleared = await clear_deposit_payment_wait(
            chat.id,
            job_queue=getattr(context, "job_queue", None),
        )
        if cleared:
            await update.message.reply_text(
                "Deposit cancelled. You can start /deposit or /cashout again when ready."
            )
            return

    if not is_private and get_active_dm_flow(context) is not None:
        await update.message.reply_text(
            "No active deposit or cashout in this group to cancel.\n\n"
            "To cancel a staff flow (/report, /note, /bonus, …), send /cancel "
            "in a private chat with this bot."
        )
        return

    clear_active_flow(context)
    await update.message.reply_text(
        "No active deposit, cashout, bonus, issue report, inactive outreach send, "
        "or support note to cancel.\n\n"
        "If you already finished, you are all set — nothing else to do."
    )
