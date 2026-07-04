"""Global /cancel for /deposit, /cashout, and /bonus — respects the latest started flow."""

from __future__ import annotations

from typing import Literal

from telegram import Update
from telegram.ext import ContextTypes

FlowName = Literal["deposit", "cashout", "bonus", "issue_report", "inactive_outreach_send"]

ACTIVE_FLOW_KEY = "active_bot_flow"

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
_BONUS_ACTIVE_KEYS = (
    "bonus_admin_id",
    "bonus_draft_id",
    "bonus_group_title",
    "bonus_gg_player_id",
    "bonus_amount",
)
_ISSUE_REPORT_ACTIVE_KEYS = (
    "ir_draft_id",
    "ir_club_id",
    "ir_category",
    "ir_title",
    "ir_details",
)
_INACTIVE_OUTREACH_SEND_KEYS = (
    "io_club_key",
    "io_message",
    "io_admin_id",
)


def mark_active_flow(context: ContextTypes.DEFAULT_TYPE, flow: FlowName) -> None:
    context.user_data[ACTIVE_FLOW_KEY] = flow


def clear_active_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(ACTIVE_FLOW_KEY, None)


def deposit_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return any(k in context.chat_data for k in _DEPOSIT_ACTIVE_KEYS)


def cashout_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return any(k in context.chat_data for k in _CASHOUT_ACTIVE_KEYS)


def bonus_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    if context.user_data.get("bonus_step"):
        return True
    return any(k in context.user_data for k in _BONUS_ACTIVE_KEYS)


def issue_report_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return any(k in context.user_data for k in _ISSUE_REPORT_ACTIVE_KEYS)


def inactive_outreach_send_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    from bot.handlers.inactive_outreach_send import sendinactive_flow_active

    if sendinactive_flow_active(context):
        return True
    return any(k in context.user_data for k in _INACTIVE_OUTREACH_SEND_KEYS)


def _cancel_order(context: ContextTypes.DEFAULT_TYPE) -> list[FlowName]:
    """Prefer the flow the user started most recently, then other active flows."""
    latest = context.user_data.get(ACTIVE_FLOW_KEY)
    order: list[FlowName] = []
    if latest in ("deposit", "cashout", "bonus", "issue_report", "inactive_outreach_send"):
        order.append(latest)
    for name in ("bonus", "issue_report", "inactive_outreach_send", "deposit", "cashout"):
        if name not in order:
            order.append(name)
    return order


async def flow_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel the active deposit, cashout, or bonus flow."""
    if not update.message:
        return

    for flow in _cancel_order(context):
        if flow == "inactive_outreach_send" and inactive_outreach_send_flow_active(context):
            from bot.handlers.inactive_outreach_send import sendinactive_cancel

            await sendinactive_cancel(update, context)
            return
        if flow == "issue_report" and issue_report_flow_active(context):
            from bot.handlers.issue_reports import issue_report_cancel

            await issue_report_cancel(update, context)
            return
        if flow == "bonus" and bonus_flow_active(context):
            from bot.handlers.bonus import bonus_cancel

            await bonus_cancel(update, context)
            return
        if flow == "deposit" and deposit_flow_active(context):
            from bot.handlers.deposit import deposit_cancel

            await deposit_cancel(update, context)
            return
        if flow == "cashout" and cashout_flow_active(context):
            from bot.handlers.cashout import cashout_cancel

            await cashout_cancel(update, context)
            return

    clear_active_flow(context)
    await update.message.reply_text(
        "No active deposit, cashout, bonus, issue report, or inactive outreach send to cancel.\n\n"
        "If you already finished, you are all set — nothing else to do."
    )
