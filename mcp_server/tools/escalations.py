"""Escalation observability: Slack escalation events and idle-escalation episodes."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from mcp.server.mcpserver import MCPServer

from api.routes.escalation_events import _event_read
from db.models import EscalationEpisode, EscalationEvent
from mcp_server.dates import parse_range
from mcp_server.db import read_session
from mcp_server.envelope import page, single
from mcp_server.errors import bad_input, not_found, translate_errors
from mcp_server.tools._common import (
    READ_ONLY,
    clamp_limit,
    clamp_offset,
    club_name_map,
    resolve_club,
)

_TRIGGER_NOTE = (
    "trigger_messages hold the player/staff chat text that fired the escalation"
)


def escalation_reasons() -> tuple[str, ...]:
    """All ``REASON_*`` values defined by the notifier, collected dynamically."""
    from bot.services import escalation_notification as mod

    values = sorted(
        {
            str(getattr(mod, name))
            for name in dir(mod)
            if name.startswith("REASON_") and isinstance(getattr(mod, name), str)
        }
    )
    return tuple(values)


def _validate_reason(reason: str | None) -> str | None:
    if reason is None or not reason.strip():
        return None
    text = reason.strip().lower()
    allowed = escalation_reasons()
    if text not in allowed:
        raise bad_input(f"reason must be one of: {', '.join(allowed)}")
    return text


def _parse_episode_id(value: str | None) -> UUID | None:
    if value is None or not str(value).strip():
        return None
    try:
        return UUID(str(value).strip())
    except ValueError as exc:
        raise bad_input(f"episode_id must be a UUID, got {value!r}") from exc


def _event_item(row: EscalationEvent, names: dict[int, str]) -> dict[str, Any]:
    data = _event_read(row).model_dump()
    club_id = data.get("club_id")
    data["club_name"] = names.get(int(club_id)) if club_id is not None else None
    return data


@translate_errors
def list_escalation_events(
    club: str | None = None,
    chat_id: int | None = None,
    reason: str | None = None,
    episode_id: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    limit: int = 50,
    offset: int = 0,
    include_pii: bool = False,
) -> dict[str, Any]:
    """Slack escalation notifications sent for support groups, newest first: reason
    (player_idle, cashout_started, deposit_sent_timeout, earlyrb_*, rpa_*, ...),
    chat, slack_ok, head_admin_fanout and the trigger messages. from/to are UTC
    datetimes (YYYY-MM-DD = that America/New_York day). `episode_id` narrows to one
    idle-escalation episode (see get_escalation_episode). Club: Club id (int), key (round_table|creator_club|clubgto) or slug (round-table|creator-club|clubgto|aces-table). Omit for all clubs."""
    reason_norm = _validate_reason(reason)
    episode_uuid = _parse_episode_id(episode_id)
    start, stop = parse_range(from_, to)
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    with read_session() as session:
        club_row = resolve_club(session, club)
        query = session.query(EscalationEvent)
        if club_row is not None:
            query = query.filter(EscalationEvent.club_id == club_row.id)
        if chat_id is not None:
            query = query.filter(EscalationEvent.telegram_chat_id == int(chat_id))
        if reason_norm is not None:
            query = query.filter(EscalationEvent.reason == reason_norm)
        if episode_uuid is not None:
            query = query.filter(EscalationEvent.episode_id == episode_uuid)
        if start is not None:
            query = query.filter(EscalationEvent.created_at >= start)
        if stop is not None:
            query = query.filter(EscalationEvent.created_at <= stop)
        total = query.order_by(None).count()
        rows = (
            query.order_by(EscalationEvent.created_at.desc(), EscalationEvent.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        names = club_name_map(session)
        items = [_event_item(r, names) for r in rows]
    return page(
        items,
        total=total,
        limit=limit,
        offset=offset,
        include_pii=include_pii,
        notes=[_TRIGGER_NOTE],
    )


@translate_errors
def get_escalation_episode(
    episode_id: str, include_pii: bool = False
) -> dict[str, Any]:
    """One idle-escalation episode (opened when a player reached out and staff were
    silent, closed when staff replied or it timed out) with its trigger messages and
    every escalation event it produced, oldest first."""
    episode_uuid = _parse_episode_id(episode_id)
    if episode_uuid is None:
        raise bad_input("episode_id is required")
    with read_session() as session:
        row = session.get(EscalationEpisode, episode_uuid)
        if row is None:
            raise not_found(f"escalation episode {episode_id}")
        events = (
            session.query(EscalationEvent)
            .filter(EscalationEvent.episode_id == episode_uuid)
            .order_by(EscalationEvent.created_at.asc(), EscalationEvent.id.asc())
            .all()
        )
        names = club_name_map(session)
        triggers = (
            row.trigger_messages if isinstance(row.trigger_messages, list) else []
        )
        item = {
            "id": row.id,
            "telegram_chat_id": int(row.telegram_chat_id),
            "club_id": int(row.club_id) if row.club_id is not None else None,
            "club_name": names.get(int(row.club_id))
            if row.club_id is not None
            else None,
            "group_title": row.group_title,
            "opened_at": row.opened_at,
            "closed_at": row.closed_at,
            "close_reason": row.close_reason,
            "trigger_messages": list(triggers),
            "events": [_event_item(e, names) for e in events],
        }
    return single(item, include_pii=include_pii, notes=[_TRIGGER_NOTE])


def register(mcp: MCPServer) -> None:
    mcp.tool(name="list_escalation_events", annotations=READ_ONLY)(
        list_escalation_events
    )
    mcp.tool(name="get_escalation_episode", annotations=READ_ONLY)(
        get_escalation_episode
    )
