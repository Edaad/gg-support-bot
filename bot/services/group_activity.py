"""Shared support-group human activity detection (silence, player vs staff)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from telegram import User

from bot.services.club import is_club_staff
from club_gc_settings import get_club_gc_config_by_link_club_id, get_gc_users_to_add
from config import ADMIN_USER_IDS

logger = logging.getLogger(__name__)

HumanRole = Literal["player", "staff"]

ESCALATION_SILENCE_SECONDS = 600  # 10 minutes

# Phrase-in-message payment confirmation (not whole-message-only).
PAYMENT_CONFIRM_RE = re.compile(
    r"(?i)"
    r"(?:"
    r"(?:(?:it'?s|i|just|have|already|\$?\d+(?:\.\d{1,2})?)\s+)*"
    r"(?:"
    r"sent(?:\s+(?:it|payment|\$?\d+(?:\.\d{1,2})?|(?:and\s+)?(?:completed|went\s+through|done)))?"
    r"|paid(?:\s+(?:it|payment|\$?\d+(?:\.\d{1,2})?))?"
    r"|made\s+the\s+payment"
    r"|send\s+already"
    r"|(?:money|payment)\s+sent"
    r")"
    r"|(?:all\s+)?done"
    r")"
)


@dataclass
class ChatActivityState:
    last_human_at: datetime | None = None
    last_human_role: HumanRole | None = None
    idle_episode_fired: bool = False
    # Deposit instructions delivered; waiting for sent/media to arm 5m watch.
    deposit_instructions_pending: bool = False
    # After sent/media: 5m payment wait armed (job scheduled separately).
    deposit_sent_watch_armed: bool = False


_chat_state: dict[int, ChatActivityState] = {}


def clear_activity_state_for_tests() -> None:
    """Test helper."""
    _chat_state.clear()


def get_chat_activity_state(chat_id: int) -> ChatActivityState:
    cid = int(chat_id)
    state = _chat_state.get(cid)
    if state is None:
        state = ChatActivityState()
        _chat_state[cid] = state
    return state


def is_support_sender(user: User | None, club_id: int) -> bool:
    """True for club staff, admins, and /gc invite accounts (never the player)."""
    if user is None:
        return True
    if getattr(user, "is_bot", False):
        return True
    uid = int(user.id)
    if uid in ADMIN_USER_IDS:
        return True
    if is_club_staff(uid, club_id):
        return True

    cfg = get_club_gc_config_by_link_club_id(int(club_id))
    if cfg is None:
        return False
    if cfg.command_admin_user_id and uid == int(cfg.command_admin_user_id):
        return True

    markers: list[str] = list(get_gc_users_to_add(cfg))
    if cfg.bot_account:
        markers.append(str(cfg.bot_account))

    un = (user.username or "").strip().lower().lstrip("@")
    for raw in markers:
        m = str(raw).strip()
        if not m:
            continue
        if m.isdigit() and int(m) == uid:
            return True
        if un and m.lstrip("@").lower() == un:
            return True
    return False


def is_payment_confirm_text(text: str | None) -> bool:
    """True when text contains a short payment-confirmation phrase."""
    if not text:
        return False
    return bool(PAYMENT_CONFIRM_RE.search(text.strip()))


def message_has_media(message: object | None) -> bool:
    """True when the Telegram message carries any media attachment."""
    if message is None:
        return False
    return bool(
        getattr(message, "photo", None)
        or getattr(message, "video", None)
        or getattr(message, "document", None)
        or getattr(message, "animation", None)
        or getattr(message, "voice", None)
        or getattr(message, "video_note", None)
        or getattr(message, "audio", None)
        or getattr(message, "sticker", None)
    )


@dataclass
class ActivityObservation:
    """Result of recording one human group message."""

    role: HumanRole
    silence_elapsed: bool
    should_fire_idle: bool
    previous_role: HumanRole | None = None


def record_human_message(
    chat_id: int,
    *,
    role: HumanRole,
    now: datetime | None = None,
    silence_seconds: int = ESCALATION_SILENCE_SECONDS,
) -> ActivityObservation:
    """Update per-chat activity and decide whether idle escalation may fire.

    Cold start: first observed human never fires (no prior timestamp).
    After ``silence_seconds`` with no humans, the next player message may fire
    once per episode. Staff→player without that silence never fires idle.
    """
    state = get_chat_activity_state(chat_id)
    ts = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    previous_role = state.last_human_role
    silence_elapsed = False
    if state.last_human_at is not None:
        delta = (ts - state.last_human_at).total_seconds()
        silence_elapsed = delta >= float(silence_seconds)

    if silence_elapsed:
        state.idle_episode_fired = False

    should_fire_idle = (
        role == "player"
        and state.last_human_at is not None
        and silence_elapsed
        and not state.idle_episode_fired
    )

    if should_fire_idle:
        state.idle_episode_fired = True

    state.last_human_at = ts
    state.last_human_role = role

    return ActivityObservation(
        role=role,
        silence_elapsed=silence_elapsed,
        should_fire_idle=should_fire_idle,
        previous_role=previous_role,
    )


def mark_deposit_instructions_pending(chat_id: int) -> None:
    state = get_chat_activity_state(chat_id)
    state.deposit_instructions_pending = True
    state.deposit_sent_watch_armed = False


def clear_deposit_instructions_pending(chat_id: int) -> None:
    state = get_chat_activity_state(chat_id)
    state.deposit_instructions_pending = False
    state.deposit_sent_watch_armed = False


def mark_deposit_sent_watch_armed(chat_id: int) -> None:
    state = get_chat_activity_state(chat_id)
    state.deposit_sent_watch_armed = True


def clear_deposit_sent_watch_armed(chat_id: int) -> None:
    state = get_chat_activity_state(chat_id)
    state.deposit_sent_watch_armed = False


def deposit_instructions_pending(chat_id: int) -> bool:
    return bool(get_chat_activity_state(chat_id).deposit_instructions_pending)


def deposit_sent_watch_armed(chat_id: int) -> bool:
    return bool(get_chat_activity_state(chat_id).deposit_sent_watch_armed)
