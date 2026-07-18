"""Helpers for group-chat ticket list enrichment and message role tagging."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from bot.services.group_chat_analysis import role_lists_for_club

DurationSource = Literal["resolution", "message_span"]


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


def assign_message_role(
    msg: dict[str, Any],
    *,
    admin_names: list[str],
    bot_names: list[str],
) -> Literal["customer", "admin", "bot"]:
    if bool(msg.get("is_bot")):
        return "bot"
    username = _norm_handle(msg.get("username") if isinstance(msg.get("username"), str) else None)
    sender = _norm_handle(
        msg.get("sender_name") if isinstance(msg.get("sender_name"), str) else None
    )
    bot_set = {_norm_handle(n) for n in bot_names}
    admin_set = {_norm_handle(n) for n in admin_names}
    if username and username in bot_set:
        return "bot"
    if sender and sender in bot_set:
        return "bot"
    if username and username in admin_set:
        return "admin"
    if sender and sender in admin_set:
        return "admin"
    return "customer"


def slice_ticket_messages(
    *,
    messages: list[Any] | None,
    message_ids: list[Any],
    club_id: int,
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
        role = assign_message_role(src, admin_names=admin_names, bot_names=bot_names)
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
