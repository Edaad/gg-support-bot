"""Shared support-group human activity detection (silence, player vs staff)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from telegram import User

from bot.services.club import is_club_staff
from bot.services.support_group_chats import (
    fetch_support_group_chat_by_telegram_chat_id,
    update_support_group_chat_row,
)
from club_gc_settings import get_club_gc_config_by_link_club_id, get_gc_users_to_add
from config import ADMIN_USER_IDS

logger = logging.getLogger(__name__)

HumanRole = Literal["player", "staff"]

ESCALATION_SILENCE_SECONDS = 600  # 10 minutes


@dataclass
class ChatActivityState:
    last_human_at: datetime | None = None
    last_human_role: HumanRole | None = None
    idle_episode_fired: bool = False
    # Deposit instructions delivered; waiting for "I have sent the payment" button.
    deposit_instructions_pending: bool = False
    deposit_method_slug: str | None = None
    # After button (bound path): 5m payment wait armed.
    deposit_sent_watch_armed: bool = False
    deposit_sent_armed_at: datetime | None = None
    # support_group_chats.id when durable; None = memory-only fallback.
    support_row_id: int | None = None


_chat_state: dict[int, ChatActivityState] = {}


def clear_activity_state_for_tests() -> None:
    """Test helper."""
    _chat_state.clear()


def _as_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _state_from_row(row: object) -> ChatActivityState:
    role_raw = getattr(row, "escalation_last_human_role", None)
    role: HumanRole | None = None
    if role_raw in ("player", "staff"):
        role = role_raw  # type: ignore[assignment]
    armed_at = _as_utc(getattr(row, "escalation_deposit_sent_armed_at", None))
    return ChatActivityState(
        last_human_at=_as_utc(getattr(row, "escalation_last_human_at", None)),
        last_human_role=role,
        idle_episode_fired=bool(
            getattr(row, "escalation_idle_episode_fired", False)
        ),
        deposit_instructions_pending=bool(
            getattr(row, "escalation_deposit_instructions_pending", False)
        ),
        deposit_method_slug=(
            (getattr(row, "escalation_deposit_method_slug", None) or None)
        ),
        deposit_sent_watch_armed=armed_at is not None,
        deposit_sent_armed_at=armed_at,
        support_row_id=int(getattr(row, "id")),
    )


def reload_chat_activity_state(chat_id: int) -> ChatActivityState:
    """Drop memory cache and reload from DB (or empty memory-only state)."""
    cid = int(chat_id)
    _chat_state.pop(cid, None)
    return get_chat_activity_state(cid)


def get_chat_activity_state(chat_id: int) -> ChatActivityState:
    cid = int(chat_id)
    state = _chat_state.get(cid)
    if state is not None:
        return state

    row = fetch_support_group_chat_by_telegram_chat_id(cid)
    if row is not None:
        state = _state_from_row(row)
    else:
        state = ChatActivityState()
    _chat_state[cid] = state
    return state


def _persist_activity_state(chat_id: int, state: ChatActivityState) -> None:
    """Best-effort write of escalation fields to support_group_chats."""
    row_id = state.support_row_id
    if row_id is None:
        row = fetch_support_group_chat_by_telegram_chat_id(int(chat_id))
        if row is None:
            return
        row_id = int(row.id)
        state.support_row_id = row_id

    ok, err = update_support_group_chat_row(
        row_id,
        escalation_last_human_at=state.last_human_at,
        escalation_last_human_role=state.last_human_role,
        escalation_idle_episode_fired=bool(state.idle_episode_fired),
        escalation_deposit_instructions_pending=bool(
            state.deposit_instructions_pending
        ),
        escalation_deposit_method_slug=state.deposit_method_slug,
        escalation_deposit_sent_armed_at=state.deposit_sent_armed_at,
    )
    if not ok:
        logger.debug(
            "group_activity: persist failed chat_id=%s err=%s",
            chat_id,
            err,
        )


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

    When ``last_human_at`` is unset (never recorded), treat silence as already
    elapsed so the first player message can escalate. Durable state covers
    worker restarts; null is only virgin / post-migrate groups.
    After ``silence_seconds`` with no humans, the next player message may fire
    once per episode. Staff→player without that silence never fires idle.
    """
    state = get_chat_activity_state(chat_id)
    ts = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    previous_role = state.last_human_role
    if state.last_human_at is None:
        silence_elapsed = True
    else:
        delta = (ts - state.last_human_at).total_seconds()
        silence_elapsed = delta >= float(silence_seconds)

    if silence_elapsed:
        state.idle_episode_fired = False

    should_fire_idle = (
        role == "player"
        and silence_elapsed
        and not state.idle_episode_fired
    )

    if should_fire_idle:
        state.idle_episode_fired = True

    state.last_human_at = ts
    state.last_human_role = role
    _persist_activity_state(chat_id, state)

    return ActivityObservation(
        role=role,
        silence_elapsed=silence_elapsed,
        should_fire_idle=should_fire_idle,
        previous_role=previous_role,
    )


def mark_deposit_instructions_pending(
    chat_id: int,
    *,
    method_slug: str | None = None,
) -> None:
    state = get_chat_activity_state(chat_id)
    state.deposit_instructions_pending = True
    state.deposit_method_slug = (method_slug or "").strip().lower() or None
    state.deposit_sent_watch_armed = False
    state.deposit_sent_armed_at = None
    _persist_activity_state(chat_id, state)


def clear_deposit_instructions_pending(chat_id: int) -> None:
    state = get_chat_activity_state(chat_id)
    state.deposit_instructions_pending = False
    state.deposit_method_slug = None
    state.deposit_sent_watch_armed = False
    state.deposit_sent_armed_at = None
    _persist_activity_state(chat_id, state)


def mark_deposit_sent_watch_armed(
    chat_id: int,
    *,
    armed_at: datetime | None = None,
) -> None:
    state = get_chat_activity_state(chat_id)
    ts = armed_at or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    state.deposit_sent_watch_armed = True
    state.deposit_sent_armed_at = ts
    # Keep instructions_pending true until clear so follow-ups are attributed.
    state.deposit_instructions_pending = True
    _persist_activity_state(chat_id, state)


def clear_deposit_sent_watch_armed(chat_id: int) -> None:
    state = get_chat_activity_state(chat_id)
    state.deposit_sent_watch_armed = False
    state.deposit_sent_armed_at = None
    _persist_activity_state(chat_id, state)


def deposit_instructions_pending(chat_id: int) -> bool:
    return bool(get_chat_activity_state(chat_id).deposit_instructions_pending)


def deposit_sent_watch_armed(chat_id: int) -> bool:
    state = get_chat_activity_state(chat_id)
    if state.deposit_sent_armed_at is not None:
        return True
    return bool(state.deposit_sent_watch_armed)


def deposit_sent_armed_at(chat_id: int) -> datetime | None:
    return get_chat_activity_state(chat_id).deposit_sent_armed_at


def deposit_method_slug(chat_id: int) -> str | None:
    return get_chat_activity_state(chat_id).deposit_method_slug


def list_armed_deposit_sent_chats() -> list[tuple[int, datetime]]:
    """Return (telegram_chat_id, armed_at) for restore on worker start."""
    from db.connection import get_db
    from db.models import SupportGroupChat

    out: list[tuple[int, datetime]] = []
    with get_db() as session:
        rows = (
            session.query(SupportGroupChat)
            .filter(SupportGroupChat.escalation_deposit_sent_armed_at.isnot(None))
            .all()
        )
        for row in rows:
            armed = _as_utc(row.escalation_deposit_sent_armed_at)
            if armed is None:
                continue
            out.append((int(row.telegram_chat_id), armed))
    return out
