"""Unified group activity handler: detection → popup keyboard + escalation."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from bot.handlers.flow_cancel import cashout_flow_active, deposit_flow_active
from bot.services.club import get_club_for_chat
from bot.services import group_activity as ga
from bot.services import popup_keyboard as pk
from bot.services import escalation_notification as esc

logger = logging.getLogger(__name__)


async def group_activity_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Track human group activity for popup keyboard and/or escalation notification."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user:
        return
    if chat.type not in ("group", "supergroup"):
        return
    if user.is_bot:
        return

    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        return

    popup_on = pk.popup_keyboard_eligible(
        chat.id, club_id=club_id, title=chat.title
    )
    esc_on = esc.escalation_notification_eligible(
        chat.id, club_id=club_id, title=chat.title
    )
    if not popup_on and not esc_on:
        return

    is_staff = ga.is_support_sender(user, club_id)
    role = "staff" if is_staff else "player"
    flow_cmd = pk.is_flow_command_text(message.text)
    observation = ga.record_human_message(
        chat.id, role=role, allow_idle_fire=not flow_cmd
    )
    job_queue = getattr(context, "job_queue", None)

    if is_staff:
        if esc_on:
            esc.on_staff_during_awaiting_agent(chat.id, job_queue=job_queue)
    else:
        pk.upsert_player_telegram_user_id(
            chat.id, user.id, username=user.username
        )
        pk.remember_player_message(
            context,
            user_id=user.id,
            message_id=message.message_id,
            username=user.username,
        )

        in_flow = deposit_flow_active(context) or cashout_flow_active(context)
        player_msg = esc.extract_player_message_for_slack(message)

        deposit_consumed = False
        idle_help_consumed = False
        awaiting_agent_consumed = False
        if esc_on and not flow_cmd:
            deposit_consumed = await esc.handle_deposit_sent_player_followup(
                context,
                chat.id,
                club_id=club_id,
                title=chat.title,
                message_text=player_msg,
                message=message,
            )
            if not deposit_consumed:
                deposit_consumed = await esc.handle_deposit_player_message(
                    context,
                    chat.id,
                    club_id=club_id,
                    title=chat.title,
                    message_text=player_msg,
                    message=message,
                )
            if not deposit_consumed:
                idle_help_consumed = await esc.handle_idle_help_free_text(
                    context.bot,
                    chat.id,
                    context,
                    club_id=club_id,
                    title=chat.title,
                    message_text=player_msg,
                )
            # Free-text-as-agent seeds the episode; later player msgs debounce.
            if not idle_help_consumed:
                awaiting_agent_consumed = esc.on_player_during_awaiting_agent(
                    chat.id,
                    message_text=player_msg,
                    job_queue=job_queue,
                )

        suppress_idle = (
            deposit_consumed
            or idle_help_consumed
            or awaiting_agent_consumed
            or esc.awaiting_agent_episode_active(chat.id)
        )
        if (
            esc_on
            and observation.should_fire_idle
            and not in_flow
            and not flow_cmd
            and not suppress_idle
        ):
            await esc.fire_player_idle(
                context.bot,
                chat.id,
                club_id=club_id,
                title=chat.title,
                message_text=player_msg,
                context=context,
            )

        # Popup keyboard strip on free text/media (not while in flow / commands).
        # When escalation is on, idle help prompt owns the CTA — skip popup strip.
        if popup_on and not esc_on and not flow_cmd and not in_flow:
            await pk.silent_strip_if_installed(
                context.bot,
                chat.id,
                context=context,
                post_copy=True,
            )

    # When escalation is on, idle help prompt owns the CTA — skip popup install.
    if not popup_on or esc_on:
        return

    if deposit_flow_active(context) or cashout_flow_active(context):
        pk.cancel_popup_keyboard_idle(
            chat.id, job_queue=getattr(context, "job_queue", None)
        )
        return

    if pk.payment_window_gate_pending(
        chat.id, job_queue=getattr(context, "job_queue", None)
    ):
        return

    pk.schedule_popup_keyboard_idle(context, chat.id)


def get_group_activity_handler() -> MessageHandler:
    return MessageHandler(
        filters.ChatType.GROUPS & filters.ALL & ~filters.StatusUpdate.ALL,
        group_activity_handler,
        block=False,
    )


def get_popup_keyboard_activity_handler() -> MessageHandler:
    """Back-compat alias."""
    return get_group_activity_handler()
