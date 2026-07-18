"""Helpers for group-chat ticket list enrichment and message role tagging."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from bot.services.group_chat_analysis import role_lists_for_club

DurationSource = Literal["resolution", "message_span"]

_COMPACT_RE = re.compile(r"[^a-z0-9]+")


def _parse_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        text = str(raw).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _seconds_between(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds()))


def compute_ticket_duration(
    events: dict[str, Any] | None,
    message_ids: list[Any] | None,
    messages_by_id: dict[int, dict[str, Any]] | None,
) -> tuple[int | None, DurationSource | None]:
    """Duration from customer_first→resolution, else first↔last ticket message."""

    ev = events if isinstance(events, dict) else {}
    start = _parse_ts(ev.get("customer_first_message"))
    resolution = _parse_ts(ev.get("resolution"))
    if start is not None and resolution is not None:
        return _seconds_between(start, resolution), "resolution"

    if not message_ids or not messages_by_id:
        return None, None

    dates: list[datetime] = []
    for mid in message_ids:
        try:
            key = int(mid)
        except (TypeError, ValueError):
            continue
        msg = messages_by_id.get(key)
        if not isinstance(msg, dict):
            continue
        ts = _parse_ts(msg.get("date"))
        if ts is not None:
            dates.append(ts)
    if len(dates) < 2:
        return None, None
    return _seconds_between(min(dates), max(dates)), "message_span"


def index_messages_by_id(
    messages: list[Any] | None,
) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    if not isinstance(messages, list):
        return out
    for item in messages:
        if not isinstance(item, dict):
            continue
        try:
            mid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        out[mid] = item
    return out


def _norm_handle(raw: str | None) -> str:
    text = (raw or "").strip()
    if text.startswith("@"):
        text = text[1:]
    return text.lower()


def _compact_handle(raw: str | None) -> str:
    """Lowercase alphanumerics only — maps 'Creator Club Support' ≈ CreatorClubSupport2."""

    return _COMPACT_RE.sub("", _norm_handle(raw))


def _matches_known(candidate: str | None, known: set[str]) -> bool:
    """Exact handle match, or compact prefix match (display name vs username)."""

    exact = _norm_handle(candidate)
    if exact and exact in {_norm_handle(k) for k in known}:
        return True
    compact = _compact_handle(candidate)
    if len(compact) < 6:
        return False
    for item in known:
        other = _compact_handle(item)
        if len(other) < 6:
            continue
        if compact == other or compact.startswith(other) or other.startswith(compact):
            return True
    return False


def _looks_like_staff_display(sender_name: str | None) -> bool:
    """Telegram display titles like 'Creator Club Support' / 'Round Table Support'."""

    text = (sender_name or "").strip().lower()
    if not text:
        return False
    if "support" in text:
        return True
    if text.startswith("admin") or " admin" in f" {text}":
        return True
    return False


def _matches_group_player(sender_name: str | None, group_name: str | None) -> bool:
    a = _compact_handle(sender_name)
    b = _compact_handle(group_name)
    if not a or not b or len(a) < 4:
        return False
    return a == b or a in b or b in a


def assign_message_role(
    msg: dict[str, Any],
    *,
    admin_names: list[str],
    bot_names: list[str],
    group_name: str | None = None,
) -> Literal["customer", "admin", "bot"]:
    if bool(msg.get("is_bot")):
        return "bot"
    username = msg.get("username") if isinstance(msg.get("username"), str) else None
    sender = msg.get("sender_name") if isinstance(msg.get("sender_name"), str) else None

    if _matches_known(username, set(bot_names)) or _matches_known(sender, set(bot_names)):
        return "bot"
    # Player display name often equals the support-group title — prefer that over staff heuristics.
    if _matches_group_player(sender, group_name):
        return "customer"
    if _matches_known(username, set(admin_names)) or _matches_known(sender, set(admin_names)):
        return "admin"
    if _looks_like_staff_display(sender):
        return "admin"
    return "customer"


def slice_ticket_messages(
    *,
    messages: list[Any] | None,
    message_ids: list[Any],
    club_id: int,
    group_name: str | None = None,
) -> list[dict[str, Any]]:
    """Filter transcript messages to ticket ids (chronological) and tag roles."""

    by_id = index_messages_by_id(messages)
    admin_names, bot_names = role_lists_for_club(int(club_id))
    ordered: list[dict[str, Any]] = []
    for mid in message_ids:
        try:
            key = int(mid)
        except (TypeError, ValueError):
            continue
        src = by_id.get(key)
        if src is None:
            continue
        role = assign_message_role(
            src,
            admin_names=admin_names,
            bot_names=bot_names,
            group_name=group_name,
        )
        ordered.append(
            {
                "id": key,
                "date": src.get("date"),
                "sender_id": src.get("sender_id"),
                "sender_name": src.get("sender_name"),
                "username": src.get("username"),
                "is_bot": bool(src.get("is_bot")),
                "text": src.get("text"),
                "media_type": src.get("media_type"),
                "media_filename": src.get("media_filename"),
                "role": role,
            }
        )
    return ordered


def customer_first_from_events(events: dict[str, Any] | None) -> str | None:
    if not isinstance(events, dict):
        return None
    raw = events.get("customer_first_message")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None
