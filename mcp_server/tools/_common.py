"""Helpers shared by tool modules: paging clamps, club resolution, annotations."""

from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.club_slug import CLUB_SLUG_TO_NAME, slug_for_club_name
from config import CLUB_SHORTHAND_TO_NAME
from db.models import Club
from mcp_server.errors import bad_input, not_found
from mcp_server.settings import DEFAULT_LIMIT, MAX_LIMIT

# club_key (club_gc_settings / support_group_chats.club_key) -> clubs.name
CLUB_KEY_TO_NAME: dict[str, str] = {
    "round_table": "Round Table",
    "creator_club": "Creator Club",
    "clubgto": "ClubGTO",
}
CLUB_NAME_TO_KEY: dict[str, str] = {v: k for k, v in CLUB_KEY_TO_NAME.items()}

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)
WRITE_NON_IDEMPOTENT = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)

CLUB_PARAM_DOC = (
    "Club id (int), key (round_table|creator_club|clubgto) or slug "
    "(round-table|creator-club|clubgto|aces-table). Omit for all clubs."
)


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), MAX_LIMIT))


def clamp_offset(offset: int | None) -> int:
    return max(0, int(offset or 0))


def club_name_for(club: str | int) -> str | None:
    """Map an id-less club reference (key, slug, shorthand, or name) to clubs.name."""
    raw = str(club).strip()
    if not raw:
        return None
    lowered = raw.lower()
    if lowered in CLUB_KEY_TO_NAME:
        return CLUB_KEY_TO_NAME[lowered]
    if lowered in CLUB_SLUG_TO_NAME:
        return CLUB_SLUG_TO_NAME[lowered]
    if raw.upper() in CLUB_SHORTHAND_TO_NAME:
        return CLUB_SHORTHAND_TO_NAME[raw.upper()]
    for name in CLUB_KEY_TO_NAME.values():
        if name.lower() == lowered:
            return name
    return None


def resolve_club(session: Session, club: str | int | None) -> Club | None:
    """Resolve a club parameter to a ``Club`` row; ``None`` when not given."""
    if club is None or (isinstance(club, str) and not club.strip()):
        return None
    if isinstance(club, int) or (isinstance(club, str) and club.strip().isdigit()):
        row = session.get(Club, int(club))
        if row is None:
            raise not_found(f"club id {club}")
        return row
    name = club_name_for(club)
    if name is None:
        raise bad_input(
            f"unknown club {club!r}; use an id, key (round_table|creator_club|clubgto)"
            " or slug (round-table|creator-club|clubgto|aces-table)"
        )
    row = session.query(Club).filter(func.lower(Club.name) == name.lower()).first()
    if row is None:
        raise not_found(f"no club named {name!r} in the database")
    return row


def resolve_club_id(session: Session, club: str | int | None) -> int | None:
    row = resolve_club(session, club)
    return int(row.id) if row is not None else None


def club_ref(club: Club | None) -> dict[str, Any] | None:
    """Compact club reference included in results."""
    if club is None:
        return None
    name = str(club.name or "")
    shorthands = [k for k, v in CLUB_SHORTHAND_TO_NAME.items() if v == name]
    return {
        "id": int(club.id),
        "name": name,
        "key": CLUB_NAME_TO_KEY.get(name),
        "slug": slug_for_club_name(name),
        "shorthands": shorthands,
    }


def club_name_map(session: Session) -> dict[int, str]:
    return {int(c.id): str(c.name) for c in session.query(Club.id, Club.name).all()}
