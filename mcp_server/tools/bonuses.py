"""Bonus records issued to players and the admin-configured bonus types."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from bot.services.bonus_records import list_bonus_records as _list_bonus_records
from db.models import BonusType
from mcp_server.db import read_session
from mcp_server.envelope import page
from mcp_server.errors import translate_errors
from mcp_server.tools._common import (
    READ_ONLY,
    clamp_limit,
    club_name_map,
    resolve_club_id,
)


@translate_errors
def list_bonus_records(
    club: str | None = None,
    bonus_type_id: int | None = None,
    other: bool = False,
    q: str | None = None,
    limit: int = 50,
    include_pii: bool = False,
) -> dict[str, Any]:
    """Bonuses recorded for players (via /bonus or the dashboard), newest issued
    first. `bonus_type_id` keeps one type; `other=true` keeps free-text bonuses
    with no type; `q` matches group title, player name, gg_player_id or the custom
    description. Amounts are dollars. No offset paging: narrow with filters.
    Use list_bonus_types for type ids. Club: id, key (round_table|creator_club|
    clubgto) or slug (round-table|creator-club|clubgto|aces-table); omit for all."""
    limit = clamp_limit(limit)
    with read_session() as session:
        club_id = resolve_club_id(session, club)
        names = club_name_map(session)
    rows = _list_bonus_records(
        club_id=club_id,
        bonus_type_id=int(bonus_type_id) if bonus_type_id is not None else None,
        other=bool(other),
        q=q,
        limit=limit,
    )
    items = []
    for row in rows:
        data = dict(row)
        club_ref_id = data.get("club_id")
        if data.get("club_name") is None and club_ref_id is not None:
            data["club_name"] = names.get(int(club_ref_id))
        items.append(data)
    return page(
        items,
        total=None,
        limit=limit,
        offset=0,
        include_pii=include_pii,
        notes=[
            "no offset paging; narrow with club/bonus_type_id/q",
            "amounts are dollars",
            "player_username is the player's display name (masked unless include_pii)",
        ],
    )


@translate_errors
def list_bonus_types() -> dict[str, Any]:
    """Bonus categories offered in the /bonus flow (id, name, is_active,
    sort_order), in display order. Pass an id as bonus_type_id to
    list_bonus_records."""
    with read_session() as session:
        rows = (
            session.query(BonusType).order_by(BonusType.sort_order, BonusType.id).all()
        )
        items = [
            {
                "id": int(row.id),
                "name": row.name,
                "is_active": bool(row.is_active) if row.is_active is not None else True,
                "sort_order": row.sort_order,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    return page(items, total=len(items), limit=len(items) or 1, offset=0)


def register(mcp: MCPServer) -> None:
    mcp.tool(name="list_bonus_records", annotations=READ_ONLY)(list_bonus_records)
    mcp.tool(name="list_bonus_types", annotations=READ_ONLY)(list_bonus_types)
    # write tools are added by the lead
