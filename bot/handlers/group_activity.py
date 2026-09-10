"""Unified group activity handler: detection → popup keyboard + escalation."""

from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from bot.handlers.flow_cancel import (
    cashout_flow_active,
    deposit_flow_active,
    transfer_flow_active,
)
from bot.services.club import get_club_for_chat
from bot.services import group_activity as ga
from bot.services import popup_keyboard as pk
from bot.services import escalation_notification as esc
from bot.services import support_group_idle_episode as idle_ep
from bot.services.escalation_observability import (
    DECISION_FIRED,
    DECISION_SKIPPED,
    REASON_DEPOSIT_FLOW_ANSWER,
    REASON_DEPOSIT_SENT_ACK_IGNORE,
    REASON_EMPTY_BODY,
    REASON_ESC_OFF,
    REASON_EXPECTED_FLOW,
    REASON_FLOW_CMD,
    REASON_PLAYER_IDLE_FED,
    REASON_PLAYER_IDLE_OPENED,
    REASON_STAFF_CLEARED_BURST,
    REASON_STAFF_NO_EPISODE,
    record_escalation_decision,
    trigger_message_from_telegram,
)

logger = logging.getLogger(__name__)


def _record_decision(
    *,
    decision: str,
    reason: str,
    chat_id: int,
    club_id: int | None,
    title: str | None,
    user_id: int | None,
    role: str | None,
    message_id: int | None,
    trigger: dict[str, Any] | None = None,
    episode_id: Any = None,
    escalation_event_id: int | None = None,
) -> None:
    record_escalation_decision(
        decision=decision,
        reason=reason,
        telegram_chat_id=int(chat_id),
        club_id=club_id,
        group_title=title,
        telegram_user_id=user_id,
        role=role,
        telegram_message_id=message_id,
        trigger_messages=[trigger] if trigger else None,
        episode_id=episode_id,
        escalation_event_id=escalation_event_id,
    )


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
    # Keep last-human timestamps for popup / legacy columns; idle fire is episode-based.
    ga.record_human_message(
        chat.id, role=role, allow_idle_fire=False
    )

    jq = getattr(context, "job_queue", None)
    mid = getattr(message, "message_id", None)
    trigger = trigger_message_from_telegram(message)

    if not esc_on:
        _record_decision(
            decision=DECISION_SKIPPED,
            reason=REASON_ESC_OFF,
            chat_id=chat.id,
            club_id=club_id,
            title=chat.title,
            user_id=user.id,
            role=role,
            message_id=mid,
            trigger=trigger,
        )

    if is_staff:
        if esc_on and idle_ep.episode_is_open(chat.id):
            idle_ep.on_staff_human(
                chat.id, job_queue=jq, title=chat.title
            )
            _record_decision(
                decision=DECISION_SKIPPED,
                reason=REASON_STAFF_CLEARED_BURST,
                chat_id=chat.id,
                club_id=club_id,
                title=chat.title,
                user_id=user.id,
                role=role,
                message_id=mid,
                trigger=trigger,
            )
        elif esc_on:
            _record_decision(
                decision=DECISION_SKIPPED,
                reason=REASON_STAFF_NO_EPISODE,
                chat_id=chat.id,
                club_id=club_id,
                title=chat.title,
                user_id=user.id,
                role=role,
                message_id=mid,
                trigger=trigger,
            )
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

        in_flow = (
            deposit_flow_active(context)
            or cashout_flow_active(context)
            or transfer_flow_active(context)
        )
        player_msg = esc.extract_player_message_for_slack(message)

        expected = idle_ep.consume_expected_flow_input(context)
        skip_episode = bool(expected)
        skip_reason: str | None = REASON_EXPECTED_FLOW if expected else None

        deposit_consumed = False
        deposit_fire_reason: str | None = None
        if esc_on and not flow_cmd and not expected:
            deposit_consumed = await esc.handle_deposit_sent_player_followup(
                context,
                chat.id,
                club_id=club_id,
                title=chat.title,
                message_text=player_msg,
                message=message,
            )
            if deposit_consumed:
                deposit_fire_reason = esc.REASON_DEPOSIT_SENT_FOLLOWUP
            else:
                deposit_consumed = await esc.handle_deposit_player_message(
                    context,
                    chat.id,
                    club_id=club_id,
                    title=chat.title,
                    message_text=player_msg,
                    message=message,
                )
                if deposit_consumed:
                    deposit_fire_reason = esc.REASON_DEPOSIT_PLAYER_MESSAGE
            if deposit_consumed:
                result = await idle_ep.feed_or_open_episode(
                    chat.id,
                    club_id=club_id,
                    title=chat.title,
                    message_text=player_msg,
                    slack_already_sent=True,
                    job_queue=jq,
                    bot=context.bot,
                    trigger_message=trigger,
                )
                _record_decision(
                    decision=DECISION_FIRED,
                    reason=deposit_fire_reason or esc.REASON_DEPOSIT_PLAYER_MESSAGE,
                    chat_id=chat.id,
                    club_id=club_id,
                    title=chat.title,
                    user_id=user.id,
                    role=role,
                    message_id=mid,
                    trigger=trigger,
                    episode_id=result.episode_id,
                )
            elif esc.is_valid_deposit_flow_answer(context, message):
                # Amount / referral answers: no Slack, no episode.
                skip_episode = True
                skip_reason = REASON_DEPOSIT_FLOW_ANSWER
            elif esc.should_ignore_deposit_sent_followup(message) and (
                esc.deposit_open_for_player_message_escalation(context, chat.id)
                or ga.deposit_sent_watch_armed(chat.id)
            ):
                # Expected sent/done/media during deposit chase: no episode.
                skip_episode = True
                skip_reason = REASON_DEPOSIT_SENT_ACK_IGNORE

        if esc_on and flow_cmd and not deposit_consumed and not skip_episode:
            skip_episode = True
            skip_reason = REASON_FLOW_CMD

        if (
            esc_on
            and skip_episode
            and not deposit_consumed
            and skip_reason is not None
        ):
            _record_decision(
                decision=DECISION_SKIPPED,
                reason=skip_reason,
                chat_id=chat.id,
                club_id=club_id,
                title=chat.title,
                user_id=user.id,
                role=role,
                message_id=mid,
                trigger=trigger,
            )

        if (
            esc_on
            and not skip_episode
            and not flow_cmd
            and not deposit_consumed
        ):
            result = await idle_ep.on_player_reach_out(
                chat.id,
                club_id=club_id,
                title=chat.title,
                message_text=player_msg,
                reason=esc.REASON_PLAYER_IDLE,
                slack_already_sent=False,
                job_queue=jq,
                bot=context.bot,
                trigger_message=trigger,
            )
            if result.outcome == "opened":
                _record_decision(
                    decision=DECISION_FIRED,
                    reason=REASON_PLAYER_IDLE_OPENED,
                    chat_id=chat.id,
                    club_id=club_id,
                    title=chat.title,
                    user_id=user.id,
                    role=role,
                    message_id=mid,
                    trigger=trigger,
                    episode_id=result.episode_id,
                    escalation_event_id=result.escalation_event_id,
                )
            elif result.outcome == "fed":
                _record_decision(
                    decision=DECISION_FIRED,
                    reason=REASON_PLAYER_IDLE_FED,
                    chat_id=chat.id,
                    club_id=club_id,
                    title=chat.title,
                    user_id=user.id,
                    role=role,
                    message_id=mid,
                    trigger=trigger,
                    episode_id=result.episode_id,
                )
            else:
                _record_decision(
                    decision=DECISION_SKIPPED,
                    reason=REASON_EMPTY_BODY,
                    chat_id=chat.id,
                    club_id=club_id,
                    title=chat.title,
                    user_id=user.id,
                    role=role,
                    message_id=mid,
                    trigger=trigger,
                )

        # Popup keyboard strip on free text/media (not while in flow / commands).
        if popup_on and not flow_cmd and not in_flow:
            await pk.silent_strip_if_installed(
                context.bot,
                chat.id,
                context=context,
                post_copy=not esc_on,
            )

    if not popup_on:
        return

    if (
        deposit_flow_active(context)
        or cashout_flow_active(context)
        or transfer_flow_active(context)
    ):
        pk.cancel_popup_keyboard_idle(
            chat.id, job_queue=jq
        )
        return

    if pk.payment_window_gate_pending(
        chat.id, job_queue=jq
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
