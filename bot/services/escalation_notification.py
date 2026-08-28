"""Escalation notification: Slack alerts (separate from popup keyboard)."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bot.runtime_config import is_test_bot_worker
from bot.services.club import get_club_for_chat, get_group_name
from bot.services import group_activity as ga
from bot.services.popup_keyboard import group_has_gg_player_id
from db.connection import get_db
from db.models import Club

logger = logging.getLogger(__name__)

DEPOSIT_SENT_WAIT_SECONDS = 300  # 5 minutes
DEPOSIT_SENT_WAIT_SECONDS_TEST = 30

REASON_PLAYER_IDLE = "player_idle"
REASON_PLAYER_IDLE_FOLLOWUP = "player_idle_followup"
REASON_CASHOUT_STARTED = "cashout_started"
REASON_DEPOSIT_SENT_TIMEOUT = "deposit_sent_timeout"
REASON_DEPOSIT_SENT_FOLLOWUP = "deposit_sent_followup"
REASON_DEPOSIT_SENT_UNBOUND = "deposit_sent_unbound"
REASON_DEPOSIT_PLAYER_MESSAGE = "deposit_player_message"
REASON_NEW_PLAYER_ONBOARDED = "new_player_onboarded"
REASON_PLAYER_DM_REACHED_OUT = "player_dm_reached_out"
REASON_EARLYRB_REQUESTED = "earlyrb_requested"
REASON_RPA_DEPOSIT_FAILED = "rpa_deposit_failed"
REASON_RPA_CASHOUT_FAILED = "rpa_cashout_failed"
REASON_RPA_DEPOSIT_UNCERTAIN = "rpa_deposit_uncertain"
REASON_RPA_CASHOUT_UNCERTAIN = "rpa_cashout_uncertain"
REASON_AUTO_CASHOUT_ESCALATION = "auto_cashout_escalation"
REASON_UNION_DEPOSIT_FIRST = "union_deposit_first"
REASON_UNION_DEPOSIT_REPEAT = "union_deposit_repeat"

_UNION_DEPOSIT_FIRST_HEADLINE = "First-time union deposit — verify with union"
_UNION_DEPOSIT_REPEAT_HEADLINE = "Union deposit — verify and add chips"
_UNION_DEPOSIT_INSTRUCTION_FIRST = (
    "This is the first time this player is using a union method. "
    "Forward to head admins to verify with union."
)
_UNION_DEPOSIT_INSTRUCTION_REPEAT = "Please verify payment and add chips."
_UNION_DEPOSIT_INSTRUCTION_REPEAT_OPEN = (
    "Please verify payment and add chips. Prior request still unchecked."
)

_HEADLINES = {
    REASON_PLAYER_IDLE: "A player just reached out.",
    REASON_PLAYER_IDLE_FOLLOWUP: "Player follow-up.",
    REASON_CASHOUT_STARTED: "Cash out initiated.",
    REASON_DEPOSIT_SENT_TIMEOUT: (
        "5 minutes have passed since the player said they sent the payment — "
        "please look out for a payment in this group chat."
    ),
    REASON_DEPOSIT_SENT_FOLLOWUP: (
        "Player sent a message after confirming they sent the payment."
    ),
    REASON_DEPOSIT_SENT_UNBOUND: "Manual deposit request.",
    REASON_DEPOSIT_PLAYER_MESSAGE: "Player messaged during deposit.",
    REASON_NEW_PLAYER_ONBOARDED: (
        "Welcome the new player who just joined the group chat."
    ),
    REASON_PLAYER_DM_REACHED_OUT: "A player reached out in DM.",
    REASON_EARLYRB_REQUESTED: "Early rakeback requested.",
    REASON_RPA_DEPOSIT_FAILED: "RPA deposit failed — add chips manually.",
    REASON_RPA_CASHOUT_FAILED: "RPA cashout failed — claim chips manually.",
    REASON_RPA_DEPOSIT_UNCERTAIN: (
        "Deposit UNCERTAIN — verify on ClubGG (do not retry)."
    ),
    REASON_RPA_CASHOUT_UNCERTAIN: (
        "Cashout UNCERTAIN — verify on ClubGG (do not re-claim)."
    ),
    REASON_AUTO_CASHOUT_ESCALATION: (
        "Automated cashout needs a human — please assist this player."
    ),
}

# Free-text / media escalations include the triggering player message body.
# RPA uncertain includes the machine OCR/reason detail the same way.
_REASONS_WITH_MESSAGE_BODY = frozenset(
    {
        REASON_PLAYER_IDLE,
        REASON_PLAYER_IDLE_FOLLOWUP,
        REASON_DEPOSIT_SENT_FOLLOWUP,
        REASON_DEPOSIT_PLAYER_MESSAGE,
        REASON_RPA_DEPOSIT_UNCERTAIN,
        REASON_RPA_CASHOUT_UNCERTAIN,
        REASON_AUTO_CASHOUT_ESCALATION,
    }
)

AWAITING_AGENT_DEBOUNCE_SECONDS = 60
AWAITING_AGENT_DEBOUNCE_SECONDS_TEST = 5
AWAITING_AGENT_EPISODE_SECONDS = 600  # 10 minutes
AWAITING_AGENT_EPISODE_SECONDS_TEST = 60

SLACK_MESSAGE_BODY_MAX_CHARS = 500
MEDIA_ONLY_PLACEHOLDER = "(media)"

DEPOSIT_SENT_ACK_COPY = (
    "Thank you! Chips will be added as soon as we receive the payment."
)
DEPOSIT_SENT_BUTTON_LABEL = "I have sent the payment"
DEPOSIT_SENT_CALLBACK_PREFIX = "depsent"

# While the 5m wait is armed: ignore expected payment acks / proofs.
_DEPOSIT_FOLLOWUP_IGNORE_RE = re.compile(r"sent|done", re.IGNORECASE)

_escalation_app: Any | None = None


def register_escalation_notification_runtime(app: Any) -> None:
    global _escalation_app
    _escalation_app = app
    try:
        restore_deposit_sent_watches(getattr(app, "job_queue", None))
    except Exception:
        logger.warning(
            "escalation: restore deposit sent watches failed", exc_info=True
        )
    try:
        from bot.services.support_group_idle_episode import (
            register_support_group_idle_episode_runtime,
        )

        register_support_group_idle_episode_runtime(app)
    except Exception:
        logger.warning(
            "escalation: restore support-group idle episodes failed",
            exc_info=True,
        )


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


def awaiting_agent_debounce_seconds() -> int:
    if is_test_bot_worker():
        return AWAITING_AGENT_DEBOUNCE_SECONDS_TEST
    return AWAITING_AGENT_DEBOUNCE_SECONDS


def awaiting_agent_episode_seconds() -> int:
    if is_test_bot_worker():
        return AWAITING_AGENT_EPISODE_SECONDS_TEST
    return AWAITING_AGENT_EPISODE_SECONDS


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


def _slack_code_span(text: str) -> str:
    """Wrap title in backticks for Slack mobile tap-to-copy; escape inner backticks."""
    safe = (text or "").replace("`", "'")
    return f"`{safe}`"


def format_player_message_for_slack(message_text: str | None) -> str | None:
    """Normalize / truncate player free-text for Slack; None means omit body."""
    if message_text is None:
        return None
    body = (message_text or "").strip()
    if not body:
        return None
    max_len = SLACK_MESSAGE_BODY_MAX_CHARS
    if len(body) > max_len:
        body = body[: max_len - 1].rstrip() + "…"
    return body


def extract_player_message_for_slack(message) -> str:
    """Text, caption, or media placeholder from a Telegram message."""
    if message is None:
        return MEDIA_ONLY_PLACEHOLDER
    text = (getattr(message, "text", None) or "").strip()
    if text:
        return text
    caption = (getattr(message, "caption", None) or "").strip()
    if caption:
        return caption
    return MEDIA_ONLY_PLACEHOLDER


def should_ignore_deposit_sent_followup(message) -> bool:
    """True for media or text/caption containing sent/done (expected ack).

    Shared by deposit-sent chase follow-up and during-deposit free-text escalate.
    """
    if message is None:
        return False
    if ga.message_has_media(message):
        return True
    blob = " ".join(
        part
        for part in (
            (getattr(message, "text", None) or "").strip(),
            (getattr(message, "caption", None) or "").strip(),
        )
        if part
    )
    if not blob:
        return False
    return _DEPOSIT_FOLLOWUP_IGNORE_RE.search(blob) is not None


def _deposit_awaiting_referral(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True during first-deposit referral prompt (any text is a valid answer)."""
    chat_data = getattr(context, "chat_data", None) or {}
    if chat_data.get("deposit_amount") or chat_data.get("deposit_awaiting_amount"):
        return False
    if not chat_data.get("deposit_is_first"):
        return False
    settings = chat_data.get("deposit_fd_settings") or {}
    if isinstance(settings, dict):
        return bool(settings.get("referral_enabled"))
    return bool(getattr(settings, "referral_enabled", False))


def is_valid_deposit_flow_answer(
    context: ContextTypes.DEFAULT_TYPE,
    message: object | None,
) -> bool:
    """True when the message is a normal /deposit step answer (skip escalate).

    Amount answers are only skipped during the amount-entry step. Once amount is
    stored (choose-method / sub / union / setup), free text — including bare
    numbers — escalates as deposit_player_message.

    The message that successfully stored deposit_amount is skipped via
    deposit_amount_message_id so group_activity (handler group 4) does not
    false-positive escalate that same update after deposit_amount_received.
    """
    if message is None:
        return False
    text = (getattr(message, "text", None) or "").strip()
    if not text:
        text = (getattr(message, "caption", None) or "").strip()

    chat_data = getattr(context, "chat_data", None) or {}
    mid = getattr(message, "message_id", None)
    if mid is not None and mid == chat_data.get("deposit_amount_message_id"):
        return True

    if _deposit_awaiting_referral(context):
        return bool(text)

    from bot.handlers.flow_staleness import looks_like_amount

    awaiting_amount = bool(chat_data.get("deposit_awaiting_amount"))
    has_amount = chat_data.get("deposit_amount") is not None
    in_amount_step = awaiting_amount or (
        bool(chat_data.get("deposit_club_id")) and not has_amount
    )
    if in_amount_step:
        return looks_like_amount(text)
    return False


def deposit_open_for_player_message_escalation(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
) -> bool:
    """Deposit session before payment, excluding post-button armed wait.

    Armed wait is owned by deposit_sent_followup. During-deposit escalate
    shares the same sent/done/media ignore helper.
    """
    if ga.deposit_sent_watch_armed(int(chat_id)):
        return False
    from bot.handlers.flow_cancel import deposit_flow_active

    if deposit_flow_active(context):
        return True
    return ga.deposit_instructions_pending(int(chat_id))


async def handle_deposit_player_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    *,
    club_id: int | None,
    title: str | None = None,
    message_text: str | None = None,
    message: object | None = None,
) -> bool:
    """Escalate free text/media during /deposit (no button / silence required).

    Skips valid amount and referral answers, and sent/done/media (same ignore
    as the armed chase). Returns True if Slack sent (caller should skip idle).
    """
    if not deposit_open_for_player_message_escalation(context, chat_id):
        return False
    if is_valid_deposit_flow_answer(context, message):
        return False
    if should_ignore_deposit_sent_followup(message):
        return False

    from bot.services.escalation_observability import trigger_message_from_telegram

    trigger = trigger_message_from_telegram(message)
    await notify_escalation_slack(
        REASON_DEPOSIT_PLAYER_MESSAGE,
        club_id=club_id,
        chat_id=int(chat_id),
        title=title,
        message_text=message_text,
        trigger_messages=[trigger] if trigger else None,
    )
    return True


def format_escalation_slack_text(
    reason: str,
    *,
    club_id: int | None,
    chat_id: int,
    title: str | None = None,
    message_text: str | None = None,
    method_slug: str | None = None,
) -> str:
    headline = _HEADLINES.get(reason, reason)
    if reason == REASON_DEPOSIT_SENT_UNBOUND:
        slug = (method_slug or "").strip().lower()
        if slug:
            headline = (
                f"Manual deposit request — no {slug} binding for this group."
            )
        else:
            headline = (
                "Manual deposit request — no binding for the selected "
                "payment method in this group."
            )
    club = _club_display_name(club_id)
    group_title = (title or get_group_name(chat_id) or "").strip() or "(no title)"
    lines = [f"*{headline}*", f"Club: {club}", _slack_code_span(group_title)]
    if reason in _REASONS_WITH_MESSAGE_BODY:
        body = format_player_message_for_slack(message_text)
        if body:
            lines.append(body)
    return "\n".join(lines)


def _union_deposit_amount_str(amount) -> str:
    if isinstance(amount, Decimal):
        amount_normalized = amount.quantize(Decimal("0.01"))
        if amount_normalized == amount_normalized.to_integral_value():
            return f"${int(amount_normalized):,}"
        return f"${amount_normalized:,.2f}"
    return f"${amount}"


def format_union_deposit_slack_text(
    *,
    variant: str,
    club_id: int | None,
    chat_id: int,
    title: str | None,
    amount,
    method_display_name: str,
) -> str:
    from bot.services.manual_deposit_requests import UnionDepositSlackVariant

    v: UnionDepositSlackVariant = variant  # type: ignore[assignment]
    if v == "first":
        headline = _UNION_DEPOSIT_FIRST_HEADLINE
        instruction = _UNION_DEPOSIT_INSTRUCTION_FIRST
    elif v == "repeat_verified":
        headline = _UNION_DEPOSIT_REPEAT_HEADLINE
        instruction = _UNION_DEPOSIT_INSTRUCTION_REPEAT
    else:
        headline = _UNION_DEPOSIT_REPEAT_HEADLINE
        instruction = _UNION_DEPOSIT_INSTRUCTION_REPEAT_OPEN

    club = _club_display_name(club_id)
    group_title = (title or get_group_name(chat_id) or "").strip() or "(no title)"
    amount_str = _union_deposit_amount_str(amount)
    method_name = (method_display_name or "").strip() or "Unknown"
    lines = [
        f"*{headline}*",
        f"Club: {club}",
        _slack_code_span(group_title),
        f"Amount: {amount_str}",
        f"Method: {method_name}",
        "",
        instruction,
    ]
    return "\n".join(lines)


async def format_union_deposit_telegram_text(
    *,
    variant: str,
    club_id: int | None,
    chat_id: int,
    title: str | None,
    amount,
    method_display_name: str,
) -> str:
    from bot.services.manual_deposit_requests import UnionDepositSlackVariant
    from notification.formatting import format_player_id_line, resolve_and_format_group_chat_line

    v: UnionDepositSlackVariant = variant  # type: ignore[assignment]
    if v == "first":
        headline = _UNION_DEPOSIT_FIRST_HEADLINE
        instruction = _UNION_DEPOSIT_INSTRUCTION_FIRST
    elif v == "repeat_verified":
        headline = _UNION_DEPOSIT_REPEAT_HEADLINE
        instruction = _UNION_DEPOSIT_INSTRUCTION_REPEAT
    else:
        headline = _UNION_DEPOSIT_REPEAT_HEADLINE
        instruction = _UNION_DEPOSIT_INSTRUCTION_REPEAT_OPEN

    club = _club_display_name(club_id)
    group_title = (title or get_group_name(chat_id) or "").strip() or "(no title)"
    amount_str = _union_deposit_amount_str(amount)
    method_name = (method_display_name or "").strip() or "Unknown"
    lines = [
        f"<b>{html.escape(headline, quote=False)}</b>",
        "",
        f"Club: {html.escape(club, quote=False)}",
        await resolve_and_format_group_chat_line(
            group_title=group_title,
            telegram_chat_id=int(chat_id),
            club_id=club_id,
        ),
    ]
    player_line = format_player_id_line(group_title)
    if player_line:
        lines.append(player_line)
    lines.extend(
        [
            "",
            f"Amount: {html.escape(amount_str, quote=False)}",
            f"Method: {html.escape(method_name, quote=False)}",
            "",
            html.escape(instruction, quote=False),
        ]
    )
    return "\n".join(lines)


async def _notify_union_deposit_payment_chats(
    *,
    telegram_text: str,
    group_title: str | None,
) -> None:
    from notification.payment_notification_delivery import deliver_payment_notification
    from notification.payment_notification_routing import resolve_notification_chat_ids

    chat_ids = resolve_notification_chat_ids([group_title])
    if not chat_ids:
        logger.warning("union deposit: no payment notification bind chats configured")
        return
    try:
        await deliver_payment_notification(
            telegram_text,
            bind_chat_ids=chat_ids,
            include_slack_escalation=False,
        )
    except Exception:
        logger.warning(
            "union deposit: payment notification chat delivery failed titles=%r chat_ids=%s",
            group_title,
            chat_ids,
            exc_info=True,
        )


async def notify_union_deposit_request_slack(
    *,
    variant: str,
    club_id: int | None,
    chat_id: int,
    title: str | None,
    amount,
    method_display_name: str,
) -> bool:
    """Slack AMs (and head admins on first-ever) when a union manual deposit is created."""
    if is_test_bot_worker():
        return False
    if not escalation_notification_eligible(
        int(chat_id), club_id=club_id, title=title
    ):
        return False

    from bot.services.manual_deposit_requests import UnionDepositSlackVariant

    v: UnionDepositSlackVariant = variant  # type: ignore[assignment]
    reason = (
        REASON_UNION_DEPOSIT_FIRST if v == "first" else REASON_UNION_DEPOSIT_REPEAT
    )
    text = format_union_deposit_slack_text(
        variant=v,
        club_id=club_id,
        chat_id=int(chat_id),
        title=title,
        amount=amount,
        method_display_name=method_display_name,
    )
    try:
        telegram_text = await format_union_deposit_telegram_text(
            variant=v,
            club_id=club_id,
            chat_id=int(chat_id),
            title=title,
            amount=amount,
            method_display_name=method_display_name,
        )
        await _notify_union_deposit_payment_chats(
            telegram_text=telegram_text,
            group_title=title or get_group_name(int(chat_id)),
        )
    except Exception:
        logger.warning(
            "union deposit: telegram payment notification failed chat_id=%s",
            chat_id,
            exc_info=True,
        )
    return await notify_escalation_slack(
        reason,
        club_id=club_id,
        chat_id=int(chat_id),
        title=title,
        slack_text=text,
    )


async def notify_escalation_slack(
    reason: str,
    *,
    club_id: int | None,
    chat_id: int,
    title: str | None = None,
    message_text: str | None = None,
    method_slug: str | None = None,
    episode_id=None,
    trigger_messages: list | None = None,
    slack_text: str | None = None,
) -> bool:
    from bot.services.escalation_observability import (
        live_history_episode_id,
        record_escalation_event,
        update_escalation_event_slack_ok,
    )
    from bot.services.head_admin_escalation import HEAD_ADMIN_ESCALATION_REASONS

    text = slack_text or format_escalation_slack_text(
        reason,
        club_id=club_id,
        chat_id=chat_id,
        title=title,
        message_text=message_text,
        method_slug=method_slug,
    )
    resolved_episode_id = episode_id
    if resolved_episode_id is None:
        resolved_episode_id = live_history_episode_id(int(chat_id))
    fanout = reason in HEAD_ADMIN_ESCALATION_REASONS
    event_id = record_escalation_event(
        reason=reason,
        telegram_chat_id=int(chat_id),
        club_id=club_id,
        group_title=title,
        episode_id=resolved_episode_id,
        slack_ok=False,
        head_admin_fanout=fanout,
        method_slug=method_slug,
        trigger_messages=trigger_messages,
    )

    ok = False
    try:
        from bot.services.slack_ops_notify import notify_slack_escalation

        ok = await notify_slack_escalation(text, source=reason)
    except Exception:
        logger.warning(
            "escalation: slack failed reason=%s chat_id=%s",
            reason,
            chat_id,
            exc_info=True,
        )
        ok = False

    update_escalation_event_slack_ok(event_id, ok)

    # Independent fan-out for allowlisted reasons (RPA fail/uncertain → head admin).
    try:
        from bot.services.head_admin_escalation import (
            maybe_notify_head_admin_escalation,
        )

        await maybe_notify_head_admin_escalation(text, reason=reason)
    except Exception:
        logger.warning(
            "escalation: head-admin fan-out failed reason=%s chat_id=%s",
            reason,
            chat_id,
            exc_info=True,
        )

    return ok


async def offer_idle_help_prompt(
    _bot: Any,
    chat_id: int,
    *,
    club_id: int | None = None,
    title: str | None = None,
) -> bool:
    """Stub for a future in-group idle menu. Wired on episode open; no-op for now."""
    return False


async def fire_player_idle(
    _bot: Any,
    chat_id: int,
    *,
    club_id: int | None,
    title: str | None = None,
    message_text: str | None = None,
    job_queue: Any | None = None,
) -> None:
    """Open/feed support-group idle episode (Slack on open; silent in-group)."""
    from bot.services.support_group_idle_episode import on_player_reach_out

    await on_player_reach_out(
        int(chat_id),
        club_id=club_id,
        title=title,
        message_text=message_text,
        reason=REASON_PLAYER_IDLE,
        slack_already_sent=False,
        job_queue=job_queue if job_queue is not None else _resolve_job_queue(),
        bot=_bot,
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


async def notify_earlyrb_requested(
    *,
    club_id: int | None,
    chat_id: int,
    title: str | None = None,
) -> None:
    """Slack when /earlyrb is allowed (no 24h block)."""
    if not escalation_notification_eligible(
        int(chat_id), club_id=club_id, title=title
    ):
        return
    await notify_escalation_slack(
        REASON_EARLYRB_REQUESTED,
        club_id=club_id,
        chat_id=int(chat_id),
        title=title,
    )


async def notify_rpa_deposit_failed(
    *,
    club_id: int | None,
    chat_id: int,
    title: str | None = None,
) -> None:
    """Slack when ClubGG auto chip-add fails and chips need manual add."""
    if not escalation_notification_enabled(club_id):
        return
    await notify_escalation_slack(
        REASON_RPA_DEPOSIT_FAILED,
        club_id=club_id,
        chat_id=int(chat_id),
        title=title,
    )


async def notify_rpa_deposit_uncertain(
    *,
    club_id: int | None,
    chat_id: int,
    title: str | None = None,
    detail: str | None = None,
) -> None:
    """Slack when ClubGG auto chip-add is UNCERTAIN (OCR mismatch / unknown outcome)."""
    if not escalation_notification_enabled(club_id):
        return
    await notify_escalation_slack(
        REASON_RPA_DEPOSIT_UNCERTAIN,
        club_id=club_id,
        chat_id=int(chat_id),
        title=title,
        message_text=detail,
    )


async def notify_rpa_cashout_failed(
    *,
    club_id: int | None,
    chat_id: int,
    title: str | None = None,
) -> None:
    """Slack when ClubGG auto-claim fails and chips need manual claim."""
    if not escalation_notification_enabled(club_id):
        return
    await notify_escalation_slack(
        REASON_RPA_CASHOUT_FAILED,
        club_id=club_id,
        chat_id=int(chat_id),
        title=title,
    )


async def notify_rpa_cashout_uncertain(
    *,
    club_id: int | None,
    chat_id: int,
    title: str | None = None,
    detail: str | None = None,
) -> None:
    """Slack when ClubGG auto-claim is UNCERTAIN (may have claimed — do not retry)."""
    if not escalation_notification_enabled(club_id):
        return
    await notify_escalation_slack(
        REASON_RPA_CASHOUT_UNCERTAIN,
        club_id=club_id,
        chat_id=int(chat_id),
        title=title,
        message_text=detail,
    )


async def notify_auto_cashout_escalation(
    *,
    club_id: int | None,
    chat_id: int,
    title: str | None = None,
    detail: str | None = None,
    claimed_amount=None,
) -> None:
    """Slack when an automated cashout can't finish on its own and needs a human.

    Always posts (the ``enable_auto_cashout`` flag is the feature's own gate) and
    surfaces whether chips were already claimed so an AM finishes the payout
    without re-claiming.
    """
    parts: list[str] = []
    if claimed_amount is not None:
        if isinstance(claimed_amount, Decimal):
            amt = claimed_amount.quantize(Decimal("0.01"))
            amt_str = (
                f"${int(amt):,}"
                if amt == amt.to_integral_value()
                else f"${amt:,.2f}"
            )
        else:
            amt_str = f"${claimed_amount}"
        parts.append(
            f"Chips already claimed: {amt_str} — cashout NOT recorded. "
            f"Finish the payout manually; DO NOT re-claim."
        )
    if detail:
        parts.append(str(detail))
    await notify_escalation_slack(
        REASON_AUTO_CASHOUT_ESCALATION,
        club_id=club_id,
        chat_id=int(chat_id),
        title=title,
        message_text="\n".join(parts) if parts else None,
    )


def format_player_contact_label(
    *,
    display_name: str | None = None,
    username: str | None = None,
) -> str:
    """Slack-facing contact string: Name (@user), name-only, or @username."""
    name = (display_name or "").strip()
    un = (username or "").strip().lstrip("@")
    if name and un:
        return f"{name} (@{un})"
    if name:
        return name
    if un:
        return f"@{un}"
    return "(unknown)"


async def notify_new_player_onboarded(
    *,
    club_id: int | None,
    chat_id: int,
    title: str | None = None,
) -> None:
    """Slack when a new player-bound support group is created."""
    if not escalation_notification_enabled(club_id):
        return
    await notify_escalation_slack(
        REASON_NEW_PLAYER_ONBOARDED,
        club_id=club_id,
        chat_id=int(chat_id),
        title=title,
    )


async def notify_player_dm_reached_out(
    *,
    club_id: int | None,
    display_name: str | None = None,
    username: str | None = None,
) -> None:
    """Slack when an incoming player DM reuses an existing support group."""
    if not escalation_notification_enabled(club_id):
        return
    contact = format_player_contact_label(
        display_name=display_name, username=username
    )
    await notify_escalation_slack(
        REASON_PLAYER_DM_REACHED_OUT,
        club_id=club_id,
        chat_id=0,
        title=contact,
    )


def deposit_sent_button_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    DEPOSIT_SENT_BUTTON_LABEL,
                    callback_data=DEPOSIT_SENT_CALLBACK_PREFIX,
                )
            ]
        ]
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


def on_deposit_instructions_sent(
    chat_id: int,
    *,
    method_slug: str | None = None,
) -> None:
    """Mark instructions pending (button shown). Prefer offer_deposit_sent_button."""
    ga.mark_deposit_instructions_pending(int(chat_id), method_slug=method_slug)
    logger.info(
        "escalation: deposit instructions pending chat_id=%s slug=%s",
        chat_id,
        method_slug,
    )


async def offer_deposit_sent_button(
    bot: Any,
    chat_id: int,
    *,
    club_id: int | None,
    method_slug: str | None,
    title: str | None = None,
    attach_to_message_id: int | None = None,
) -> bool:
    """Show inline 'I have sent the payment' when escalation is on.

    Returns True if the button was offered.
    """
    if not escalation_notification_eligible(
        int(chat_id), club_id=club_id, title=title
    ):
        return False

    slug = (method_slug or "").strip().lower() or None

    on_deposit_instructions_sent(int(chat_id), method_slug=slug)
    markup = deposit_sent_button_markup()

    if attach_to_message_id is not None:
        try:
            await bot.edit_message_reply_markup(
                chat_id=int(chat_id),
                message_id=int(attach_to_message_id),
                reply_markup=markup,
            )
            ga.set_deposit_sent_button_message_id(
                int(chat_id), int(attach_to_message_id)
            )
            return True
        except Exception:
            logger.debug(
                "escalation: attach sent button failed chat_id=%s msg=%s; "
                "sending standalone",
                chat_id,
                attach_to_message_id,
                exc_info=True,
            )

    try:
        sent = await bot.send_message(
            chat_id=int(chat_id),
            text="\u200b",
            reply_markup=markup,
        )
        mid = getattr(sent, "message_id", None)
        if mid is not None:
            ga.set_deposit_sent_button_message_id(int(chat_id), int(mid))
        return True
    except Exception:
        logger.warning(
            "escalation: send sent button failed chat_id=%s",
            chat_id,
            exc_info=True,
        )
        return False


def on_payment_received_for_escalation(chat_id: int) -> None:
    """Payment notify landed — cancel deposit sent chase (DB-durable)."""
    cancel_deposit_sent_watch(int(chat_id))
    ga.mark_post_deposit_idle_pending(int(chat_id))


async def clear_deposit_chase_after_payment(
    bot: Any | None,
    chat_id: int,
    *,
    job_queue: Any | None = None,
) -> None:
    """Clear chase state and strip the sent button if we know its message id."""
    msg_id = ga.deposit_sent_button_message_id(int(chat_id))
    cancel_deposit_sent_watch(int(chat_id), job_queue=job_queue)
    ga.mark_post_deposit_idle_pending(int(chat_id))
    if bot is None or msg_id is None:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=int(chat_id),
            message_id=int(msg_id),
            reply_markup=None,
        )
    except Exception:
        logger.debug(
            "escalation: strip sent button failed chat_id=%s msg=%s",
            chat_id,
            msg_id,
            exc_info=True,
        )


def _payment_seen_since_arm(chat_id: int, armed_at: datetime) -> bool:
    """True if payment notify / Stripe complete / /add since arm (deposit reminder helpers)."""
    try:
        from bot.handlers.deposit import _should_skip_deposit_reminder

        return bool(_should_skip_deposit_reminder(int(chat_id), armed_at))
    except Exception:
        logger.debug(
            "escalation: payment-seen check failed chat_id=%s",
            chat_id,
            exc_info=True,
        )
        return False


async def _deposit_sent_timeout_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    if job is None or not job.data:
        return
    data = job.data
    chat_id = int(data["chat_id"])
    club_id = data.get("club_id")
    title = data.get("title")

    # Prefer durable DB state when a support_group_chats row exists (API may
    # have cleared armed_at). Memory-only chats keep in-process state.
    row = ga.fetch_support_group_chat_by_telegram_chat_id(chat_id)
    if row is not None:
        ga.reload_chat_activity_state(chat_id)
    if not ga.deposit_sent_watch_armed(chat_id):
        return

    armed_at = ga.deposit_sent_armed_at(chat_id)
    if armed_at is not None and _payment_seen_since_arm(chat_id, armed_at):
        logger.info(
            "escalation: deposit sent timeout skipped (payment seen) chat_id=%s",
            chat_id,
        )
        cancel_deposit_sent_watch(
            chat_id, job_queue=getattr(context, "job_queue", None)
        )
        return

    ga.clear_deposit_instructions_pending(chat_id)
    await notify_escalation_slack(
        REASON_DEPOSIT_SENT_TIMEOUT,
        club_id=int(club_id) if club_id is not None else None,
        chat_id=chat_id,
        title=title,
    )


def schedule_deposit_sent_watch(
    context: ContextTypes.DEFAULT_TYPE | None,
    chat_id: int,
    *,
    club_id: int | None,
    title: str | None = None,
    armed_at: datetime | None = None,
    when: float | None = None,
) -> None:
    """Start 5-minute wait after bound-method payment claim button."""
    jq = None
    if context is not None:
        jq = getattr(context, "job_queue", None)
    jq = jq or _resolve_job_queue()
    if jq is None:
        logger.warning(
            "escalation: no job_queue for deposit sent watch chat_id=%s", chat_id
        )
        # Still persist arm so restore / API cancel works.
        ga.mark_deposit_sent_watch_armed(int(chat_id), armed_at=armed_at)
        return

    name = _sent_watch_job_name(chat_id)
    try:
        for job in jq.get_jobs_by_name(name):
            job.schedule_removal()
    except Exception:
        pass

    ga.mark_deposit_sent_watch_armed(int(chat_id), armed_at=armed_at)
    delay = float(when) if when is not None else float(deposit_sent_wait_seconds())
    if delay < 0:
        delay = 0.0
    jq.run_once(
        _deposit_sent_timeout_callback,
        when=delay,
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
        delay,
    )


def restore_deposit_sent_watches(job_queue: Any | None = None) -> None:
    """Re-schedule or immediately fire armed watches after worker restart."""
    jq = _resolve_job_queue(job_queue)
    if jq is None:
        return
    now = datetime.now(timezone.utc)
    wait = float(deposit_sent_wait_seconds())
    for chat_id, armed_at in ga.list_armed_deposit_sent_chats():
        if _payment_seen_since_arm(chat_id, armed_at):
            cancel_deposit_sent_watch(chat_id, job_queue=jq)
            continue
        elapsed = (now - armed_at).total_seconds()
        remaining = wait - elapsed
        club_id = get_club_for_chat(chat_id)
        title = get_group_name(chat_id)
        ga.reload_chat_activity_state(int(chat_id))
        schedule_deposit_sent_watch(
            None,
            int(chat_id),
            club_id=club_id,
            title=title,
            armed_at=armed_at,
            when=remaining,
        )


async def handle_deposit_sent_player_followup(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    *,
    club_id: int | None,
    title: str | None = None,
    message_text: str | None = None,
    message: object | None = None,
) -> bool:
    """If 5m wait is armed, escalate on non-ack player text.

    Media or text/caption containing ``sent`` / ``done`` is ignored: no Slack
    for follow-up, wait stays armed, and returns False so player-idle can still
    fire when silence criteria are met. Any other text Slack-escalates follow-up
    and cancels the wait (returns True so idle is skipped).

    Returns True if consumed (caller should skip idle escalation).
    """
    if not ga.deposit_sent_watch_armed(chat_id):
        return False

    if should_ignore_deposit_sent_followup(message):
        return False

    cancel_deposit_sent_watch(
        chat_id, job_queue=getattr(context, "job_queue", None)
    )
    from bot.services.escalation_observability import trigger_message_from_telegram

    trigger = trigger_message_from_telegram(message)
    await notify_escalation_slack(
        REASON_DEPOSIT_SENT_FOLLOWUP,
        club_id=club_id,
        chat_id=int(chat_id),
        title=title,
        message_text=message_text,
        trigger_messages=[trigger] if trigger else None,
    )
    return True


async def handle_deposit_sent_claim(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Callback for inline 'I have sent the payment' button."""
    query = update.callback_query
    if query is None:
        return
    data = (query.data or "").strip()
    if data != DEPOSIT_SENT_CALLBACK_PREFIX:
        return

    message = query.message
    chat = update.effective_chat
    if message is None or chat is None:
        await query.answer()
        return

    chat_id = int(chat.id)
    club_id = get_club_for_chat(chat_id)
    title = getattr(chat, "title", None)

    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        logger.debug(
            "escalation: remove sent button failed chat_id=%s",
            chat_id,
            exc_info=True,
        )

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=DEPOSIT_SENT_ACK_COPY,
        )
    except Exception:
        logger.warning(
            "escalation: deposit sent ack failed chat_id=%s",
            chat_id,
            exc_info=True,
        )

    if not escalation_notification_eligible(
        chat_id, club_id=club_id, title=title
    ):
        ga.clear_deposit_instructions_pending(chat_id)
        return

    if not ga.deposit_instructions_pending(chat_id) and not ga.deposit_sent_watch_armed(
        chat_id
    ):
        # Stale button after cancel; still acked above.
        return

    slug = ga.deposit_method_slug(chat_id)
    bound = False
    if slug:
        try:
            from bot.services.payment_method_binding import (
                chat_has_deposit_method_binding,
            )

            bound = chat_has_deposit_method_binding(chat_id, slug)
        except Exception:
            logger.debug(
                "escalation: binding lookup failed chat_id=%s slug=%s",
                chat_id,
                slug,
                exc_info=True,
            )

    if not bound:
        cancel_deposit_sent_watch(
            chat_id, job_queue=getattr(context, "job_queue", None)
        )
        await notify_escalation_slack(
            REASON_DEPOSIT_SENT_UNBOUND,
            club_id=club_id,
            chat_id=chat_id,
            title=title,
            method_slug=slug,
        )
        return

    schedule_deposit_sent_watch(
        context, chat_id, club_id=club_id, title=title
    )


def get_deposit_sent_claim_handler() -> CallbackQueryHandler:
    return CallbackQueryHandler(
        handle_deposit_sent_claim,
        pattern=rf"^{DEPOSIT_SENT_CALLBACK_PREFIX}$",
    )


# Back-compat name used by older tests / callers.
async def handle_deposit_sent_player_signal(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    *,
    club_id: int | None,
    title: str | None = None,
    is_confirm_signal: bool = False,
    message_text: str | None = None,
    message: object | None = None,
) -> bool:
    """Deprecated alias: follow-up-only when armed (with sent/done/media ignore)."""
    if is_confirm_signal and not ga.deposit_sent_watch_armed(chat_id):
        return False
    return await handle_deposit_sent_player_followup(
        context,
        chat_id,
        club_id=club_id,
        title=title,
        message_text=message_text,
        message=message,
    )
