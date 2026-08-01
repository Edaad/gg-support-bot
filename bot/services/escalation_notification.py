"""Escalation notification: player idle ack + Slack (separate from popup keyboard)."""

from __future__ import annotations

import logging
from typing import Any

from telegram.ext import ContextTypes

from bot.runtime_config import is_test_bot_worker
from bot.services.club import get_club_for_chat, get_group_name
from bot.services import group_activity as ga
from bot.services.popup_keyboard import (
    group_has_gg_player_id,
    get_popup_keyboard_installed,
    set_popup_keyboard_installed,
    remove_markup,
)
from db.connection import get_db
from db.models import Club

logger = logging.getLogger(__name__)

ACK_COPY = "We'll be with you in just a second."
DEPOSIT_SENT_WAIT_SECONDS = 300  # 5 minutes
DEPOSIT_SENT_WAIT_SECONDS_TEST = 30

REASON_PLAYER_IDLE = "player_idle"
REASON_CASHOUT_STARTED = "cashout_started"
REASON_DEPOSIT_SENT_TIMEOUT = "deposit_sent_timeout"
REASON_DEPOSIT_SENT_FOLLOWUP = "deposit_sent_followup"

_HEADLINES = {
    REASON_PLAYER_IDLE: "A player just reached out.",
    REASON_CASHOUT_STARTED: "Cash out initiated.",
    REASON_DEPOSIT_SENT_TIMEOUT: "Deposit payment not seen.",
    REASON_DEPOSIT_SENT_FOLLOWUP: "Deposit follow-up after payment claim.",
}

_escalation_app: Any | None = None


def register_escalation_notification_runtime(app: Any) -> None:
    global _escalation_app
    _escalation_app = app


def _resolve_job_queue(job_queue: Any | None = None) -> Any | None:
    if job_queue is not None:
        return job_queue
    if _escalation_app is not None:
        return getattr(_escalation_app, "job_queue", None)
    return None


def deposit_sent_wait_seconds() -> int:
    if is_test_bot_worker():
        return DEPOSIT_SENT_WAIT_SECONDS_TEST
    return DEPOSIT_SENT_WAIT_SECONDS


def _sent_watch_job_name(chat_id: int | str) -> str:
    return f"escalation_deposit_sent_{chat_id}"


def escalation_notification_enabled(club_id: int | None) -> bool:
    """True when club flag is on (main and test bot both respect the flag)."""
    if club_id is None:
        return False
    with get_db() as session:
        club = session.get(Club, int(club_id))
        if club is None:
            return False
        return bool(getattr(club, "enable_escalation_notification", False))


def escalation_notification_eligible(
    chat_id: int,
    *,
    club_id: int | None = None,
    title: str | None = None,
) -> bool:
    cid = club_id if club_id is not None else get_club_for_chat(chat_id)
    if not escalation_notification_enabled(cid):
        return False
    return group_has_gg_player_id(chat_id, title=title)


def _club_display_name(club_id: int | None) -> str:
    if club_id is None:
        return "Unknown"
    with get_db() as session:
        club = session.get(Club, int(club_id))
        if club is None:
            return f"club:{club_id}"
        return (club.name or "").strip() or f"club:{club_id}"


def format_escalation_slack_text(
    reason: str,
    *,
    club_id: int | None,
    chat_id: int,
    title: str | None = None,
) -> str:
    headline = _HEADLINES.get(reason, reason)
    club = _club_display_name(club_id)
    group_title = (title or get_group_name(chat_id) or "").strip() or "(no title)"
    return (
        f"{headline}\n"
        f"Club: {club}\n"
        f"Group: {group_title} ({chat_id})"
    )


async def notify_escalation_slack(
    reason: str,
    *,
    club_id: int | None,
    chat_id: int,
    title: str | None = None,
) -> bool:
    text = format_escalation_slack_text(
        reason, club_id=club_id, chat_id=chat_id, title=title
    )
    try:
        from bot.services.slack_ops_notify import notify_slack_escalation

        return await notify_slack_escalation(text, source=reason)
    except Exception:
        logger.warning(
            "escalation: slack failed reason=%s chat_id=%s",
            reason,
            chat_id,
            exc_info=True,
        )
        return False


async def send_player_ack(
    bot: Any,
    chat_id: int,
    *,
    strip_keyboard: bool = False,
) -> bool:
    """Post the player-facing ack; optionally attach ReplyKeyboardRemove."""
    kwargs: dict[str, Any] = {"chat_id": int(chat_id), "text": ACK_COPY}
    if strip_keyboard:
        kwargs["reply_markup"] = remove_markup()
    try:
        await bot.send_message(**kwargs)
        if strip_keyboard:
            set_popup_keyboard_installed(int(chat_id), False)
        return True
    except Exception:
        logger.warning(
            "escalation: ack send failed chat_id=%s", chat_id, exc_info=True
        )
        return False


async def fire_player_idle(
    bot: Any,
    chat_id: int,
    *,
    club_id: int | None,
    title: str | None = None,
) -> None:
    strip = get_popup_keyboard_installed(int(chat_id))
    await send_player_ack(bot, chat_id, strip_keyboard=strip)
    await notify_escalation_slack(
        REASON_PLAYER_IDLE,
        club_id=club_id,
        chat_id=int(chat_id),
        title=title,
    )


async def notify_cashout_started(
    *,
    club_id: int | None,
    chat_id: int,
    title: str | None = None,
) -> None:
    if not escalation_notification_eligible(
        int(chat_id), club_id=club_id, title=title
    ):
        return
    await notify_escalation_slack(
        REASON_CASHOUT_STARTED,
        club_id=club_id,
        chat_id=int(chat_id),
        title=title,
    )


def cancel_deposit_sent_watch(
    chat_id: int | str,
    *,
    job_queue: Any | None = None,
) -> None:
    ga.clear_deposit_instructions_pending(int(chat_id))
    queue = _resolve_job_queue(job_queue)
    if queue is None:
        return
    try:
        for job in queue.get_jobs_by_name(_sent_watch_job_name(chat_id)):
            job.schedule_removal()
    except Exception:
        logger.debug(
            "escalation: cancel deposit sent watch failed chat_id=%s",
            chat_id,
            exc_info=True,
        )


def on_deposit_instructions_sent(chat_id: int) -> None:
    """Arm detection so the next sent/media starts the 5-minute payment wait."""
    ga.mark_deposit_instructions_pending(int(chat_id))
    logger.info("escalation: deposit instructions pending chat_id=%s", chat_id)


def on_payment_received_for_escalation(chat_id: int) -> None:
    """Payment notify landed — cancel deposit sent chase."""
    cancel_deposit_sent_watch(int(chat_id))


async def _deposit_sent_timeout_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    if job is None or not job.data:
        return
    data = job.data
    chat_id = int(data["chat_id"])
    club_id = data.get("club_id")
    title = data.get("title")
    if not ga.deposit_sent_watch_armed(chat_id):
        return
    ga.clear_deposit_instructions_pending(chat_id)
    await notify_escalation_slack(
        REASON_DEPOSIT_SENT_TIMEOUT,
        club_id=int(club_id) if club_id is not None else None,
        chat_id=chat_id,
        title=title,
    )


def schedule_deposit_sent_watch(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    *,
    club_id: int | None,
    title: str | None = None,
) -> None:
    """Start 5-minute wait after player sent/media confirmation."""
    jq = getattr(context, "job_queue", None) or _resolve_job_queue()
    if jq is None:
        logger.warning(
            "escalation: no job_queue for deposit sent watch chat_id=%s", chat_id
        )
        return

    name = _sent_watch_job_name(chat_id)
    try:
        for job in jq.get_jobs_by_name(name):
            job.schedule_removal()
    except Exception:
        pass

    ga.mark_deposit_sent_watch_armed(int(chat_id))
    jq.run_once(
        _deposit_sent_timeout_callback,
        when=float(deposit_sent_wait_seconds()),
        data={
            "chat_id": int(chat_id),
            "club_id": int(club_id) if club_id is not None else None,
            "title": title,
        },
        name=name,
        job_kwargs={"misfire_grace_time": 60},
    )
    logger.info(
        "escalation: deposit sent watch armed chat_id=%s wait_s=%s",
        chat_id,
        deposit_sent_wait_seconds(),
    )


async def handle_deposit_sent_player_signal(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    *,
    club_id: int | None,
    title: str | None = None,
    is_confirm_signal: bool,
) -> bool:
    """Process player sent/media or follow-up while instructions pending.

    Returns True if this message was consumed by the deposit-sent chase
    (caller should skip idle escalation).
    """
    if not ga.deposit_instructions_pending(chat_id):
        return False

    if ga.deposit_sent_watch_armed(chat_id):
        # Another player message after arm → follow-up escalate.
        cancel_deposit_sent_watch(
            chat_id, job_queue=getattr(context, "job_queue", None)
        )
        await notify_escalation_slack(
            REASON_DEPOSIT_SENT_FOLLOWUP,
            club_id=club_id,
            chat_id=int(chat_id),
            title=title,
        )
        return True

    if is_confirm_signal:
        schedule_deposit_sent_watch(
            context, int(chat_id), club_id=club_id, title=title
        )
        return True

    return False
