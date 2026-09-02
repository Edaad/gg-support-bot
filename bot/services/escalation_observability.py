"""Persist support-group Slack escalations and idle-episode history."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from db.connection import get_db
from db.models import (
    EscalationDecisionLog,
    EscalationEpisode,
    EscalationEvent,
    SupportGroupIdleEpisodeState,
)

logger = logging.getLogger(__name__)

CLOSE_REASON_SILENCE = "silence"
CLOSE_REASON_HARD_CAP = "hard_cap"
CLOSE_REASON_FLOW_END = "flow_end"

DECISION_SKIPPED = "skipped"
DECISION_FIRED = "fired"

# Skip reasons (group_activity_handler)
REASON_ESC_OFF = "escalation_off"
REASON_STAFF_NO_EPISODE = "staff_no_episode"
REASON_STAFF_CLEARED_BURST = "staff_cleared_burst"
REASON_EMPTY_BODY = "empty_body"
REASON_EXPECTED_FLOW = "expected_flow"
REASON_FLOW_CMD = "flow_cmd"
REASON_DEPOSIT_FLOW_ANSWER = "deposit_flow_answer"
REASON_DEPOSIT_SENT_ACK_IGNORE = "deposit_sent_ack_ignore"

# Fire reasons (group_activity_handler)
REASON_PLAYER_IDLE_OPENED = "player_idle_opened"
REASON_PLAYER_IDLE_FED = "player_idle_fed"
# deposit_player_message / deposit_sent_followup reuse escalation_notification reason slugs


_MEDIA_KINDS = (
    "photo",
    "video",
    "document",
    "animation",
    "voice",
    "video_note",
    "audio",
    "sticker",
)


def _truncate_text(text: str | None, max_chars: int) -> str:
    body = (text or "").strip()
    if len(body) <= max_chars:
        return body
    if max_chars <= 1:
        return "…"
    return body[: max_chars - 1].rstrip() + "…"


def trigger_message_from_telegram(message: object | None) -> dict[str, Any] | None:
    """Build one trigger_messages entry from a Telegram Message (or similar)."""
    if message is None:
        return None
    from bot.services.escalation_notification import (
        MEDIA_ONLY_PLACEHOLDER,
        SLACK_MESSAGE_BODY_MAX_CHARS,
    )

    text = (getattr(message, "text", None) or "").strip()
    caption = (getattr(message, "caption", None) or "").strip()
    body = text or caption
    media_kind = None
    for kind in _MEDIA_KINDS:
        if getattr(message, kind, None):
            media_kind = kind
            break
    has_media = media_kind is not None
    if not body and has_media:
        body = MEDIA_ONLY_PLACEHOLDER
    if not body and not has_media:
        msg_id = getattr(message, "message_id", None)
        if msg_id is None:
            return None

    user = getattr(message, "from_user", None)
    date = getattr(message, "date", None)
    message_at = None
    if isinstance(date, datetime):
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        message_at = date.astimezone(timezone.utc).isoformat()

    entry: dict[str, Any] = {
        "telegram_message_id": getattr(message, "message_id", None),
        "telegram_user_id": getattr(user, "id", None) if user is not None else None,
        "username": getattr(user, "username", None) if user is not None else None,
        "display_name": None,
        "text": _truncate_text(body, SLACK_MESSAGE_BODY_MAX_CHARS) if body else None,
        "has_media": has_media,
        "media_kind": media_kind,
        "message_at": message_at,
    }
    if user is not None:
        first = (getattr(user, "first_name", None) or "").strip()
        last = (getattr(user, "last_name", None) or "").strip()
        name = " ".join(p for p in (first, last) if p).strip() or None
        entry["display_name"] = name
    return entry


def normalize_trigger_messages(
    items: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not items:
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append(item)
    return out


def live_history_episode_id(chat_id: int) -> UUID | None:
    try:
        with get_db() as session:
            row = session.get(SupportGroupIdleEpisodeState, int(chat_id))
            if row is None or row.episode_started_at is None:
                return None
            return row.history_episode_id
    except Exception:
        logger.debug(
            "escalation_observability: live episode lookup failed chat_id=%s",
            chat_id,
            exc_info=True,
        )
        return None


def open_history_episode(
    *,
    telegram_chat_id: int,
    club_id: int | None,
    group_title: str | None,
    trigger_messages: list[dict[str, Any]] | None = None,
    opened_at: datetime | None = None,
) -> UUID | None:
    episode_id = uuid.uuid4()
    ts = opened_at or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    triggers = normalize_trigger_messages(trigger_messages)
    try:
        with get_db() as session:
            session.add(
                EscalationEpisode(
                    id=episode_id,
                    telegram_chat_id=int(telegram_chat_id),
                    club_id=int(club_id) if club_id is not None else None,
                    group_title=group_title,
                    opened_at=ts,
                    trigger_messages=triggers,
                )
            )
        return episode_id
    except Exception:
        logger.warning(
            "escalation_observability: open episode failed chat_id=%s",
            telegram_chat_id,
            exc_info=True,
        )
        return None


def append_episode_triggers(
    episode_id: UUID | str | None,
    extra: list[dict[str, Any]] | None,
) -> None:
    if episode_id is None or not extra:
        return
    try:
        eid = episode_id if isinstance(episode_id, UUID) else UUID(str(episode_id))
        with get_db() as session:
            row = session.get(EscalationEpisode, eid)
            if row is None:
                return
            current = list(row.trigger_messages or [])
            for item in extra:
                if item and (not current or current[-1] != item):
                    current.append(item)
            row.trigger_messages = current
    except Exception:
        logger.warning(
            "escalation_observability: append triggers failed episode_id=%s",
            episode_id,
            exc_info=True,
        )


def close_history_episode(
    episode_id: UUID | str | None,
    *,
    close_reason: str,
    closed_at: datetime | None = None,
) -> None:
    if episode_id is None:
        return
    ts = closed_at or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    try:
        eid = episode_id if isinstance(episode_id, UUID) else UUID(str(episode_id))
        with get_db() as session:
            row = session.get(EscalationEpisode, eid)
            if row is None:
                return
            if row.closed_at is None:
                row.closed_at = ts
                row.close_reason = close_reason
    except Exception:
        logger.warning(
            "escalation_observability: close episode failed episode_id=%s",
            episode_id,
            exc_info=True,
        )


def record_escalation_event(
    *,
    reason: str,
    telegram_chat_id: int,
    club_id: int | None = None,
    group_title: str | None = None,
    episode_id: UUID | str | None = None,
    slack_ok: bool = False,
    head_admin_fanout: bool = False,
    method_slug: str | None = None,
    trigger_messages: list[dict[str, Any]] | None = None,
) -> int | None:
    """Insert one event. Never raises. Returns row id or None."""
    eid = None
    if episode_id is not None:
        try:
            eid = episode_id if isinstance(episode_id, UUID) else UUID(str(episode_id))
        except (ValueError, TypeError):
            eid = None
    try:
        with get_db() as session:
            row = EscalationEvent(
                reason=reason,
                club_id=int(club_id) if club_id is not None else None,
                telegram_chat_id=int(telegram_chat_id),
                group_title=group_title,
                episode_id=eid,
                slack_ok=bool(slack_ok),
                head_admin_fanout=bool(head_admin_fanout),
                method_slug=(method_slug or "").strip().lower() or None,
                trigger_messages=normalize_trigger_messages(trigger_messages),
            )
            session.add(row)
            session.flush()
            return int(row.id)
    except Exception:
        logger.warning(
            "escalation_observability: record event failed reason=%s chat_id=%s",
            reason,
            telegram_chat_id,
            exc_info=True,
        )
        return None


def update_escalation_event_slack_ok(event_id: int | None, slack_ok: bool) -> None:
    if event_id is None:
        return
    try:
        with get_db() as session:
            row = session.get(EscalationEvent, int(event_id))
            if row is None:
                return
            row.slack_ok = bool(slack_ok)
    except Exception:
        logger.warning(
            "escalation_observability: update slack_ok failed event_id=%s",
            event_id,
            exc_info=True,
        )


def record_escalation_decision(
    *,
    decision: str,
    reason: str,
    telegram_chat_id: int,
    club_id: int | None = None,
    group_title: str | None = None,
    telegram_user_id: int | None = None,
    role: str | None = None,
    telegram_message_id: int | None = None,
    trigger_messages: list[dict[str, Any]] | None = None,
    episode_id: UUID | str | None = None,
    escalation_event_id: int | None = None,
) -> int | None:
    """Insert one skip/fire decision. Never raises. Returns row id or None."""
    eid = None
    if episode_id is not None:
        try:
            eid = episode_id if isinstance(episode_id, UUID) else UUID(str(episode_id))
        except (ValueError, TypeError):
            eid = None
    try:
        with get_db() as session:
            row = EscalationDecisionLog(
                decision=(decision or "").strip() or DECISION_SKIPPED,
                reason=(reason or "").strip() or "unknown",
                club_id=int(club_id) if club_id is not None else None,
                telegram_chat_id=int(telegram_chat_id),
                group_title=group_title,
                telegram_user_id=(
                    int(telegram_user_id) if telegram_user_id is not None else None
                ),
                role=(role or "").strip() or None,
                telegram_message_id=(
                    int(telegram_message_id)
                    if telegram_message_id is not None
                    else None
                ),
                trigger_messages=normalize_trigger_messages(trigger_messages),
                episode_id=eid,
                escalation_event_id=(
                    int(escalation_event_id)
                    if escalation_event_id is not None
                    else None
                ),
            )
            session.add(row)
            session.flush()
            return int(row.id)
    except Exception:
        logger.warning(
            "escalation_observability: record decision failed decision=%s "
            "reason=%s chat_id=%s",
            decision,
            reason,
            telegram_chat_id,
            exc_info=True,
        )
        return None

