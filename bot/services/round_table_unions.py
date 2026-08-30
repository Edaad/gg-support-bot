"""Deposit/cashout unions per club and club detection.

Round Table players choose between Round Table (TMT) and Aces Table (Massiv).
Creator Club players get the same choice between Creator Club (TMT) and Aces
Table (Massiv); their first Aces Table deposit is gated behind a one-time
"join the club" link (see ``bot/handlers/deposit.py``).

The union only decides **which ClubGG club the chips land in** — payment methods
always come from the dashboard club.
"""

from __future__ import annotations

from typing import Optional, TypedDict

from bot.services.club import get_club_by_id

ROUND_TABLE_CLUB_NAME = "Round Table"
CREATOR_CLUB_CLUB_NAME = "Creator Club"
ACES_TABLE_SHORTHAND = "AT"
ROUND_TABLE_UNION_SHORTHANDS = frozenset({"RT", "AT"})


class RoundTableUnion(TypedDict):
    shorthand: str
    label: str


ROUND_TABLE_DEPOSIT_UNIONS: tuple[RoundTableUnion, ...] = (
    {"shorthand": "RT", "label": "Round Table (TMT Union)"},
    {"shorthand": "AT", "label": "Aces Table (Massiv Union)"},
)

CREATOR_CLUB_DEPOSIT_UNIONS: tuple[RoundTableUnion, ...] = (
    {"shorthand": "CC", "label": "Creator Club (TMT Union)"},
    {"shorthand": "AT", "label": "Aces Table (Massiv Union)"},
)

_UNIONS_BY_CLUB_NAME: dict[str, tuple[RoundTableUnion, ...]] = {
    ROUND_TABLE_CLUB_NAME.lower(): ROUND_TABLE_DEPOSIT_UNIONS,
    CREATOR_CLUB_CLUB_NAME.lower(): CREATOR_CLUB_DEPOSIT_UNIONS,
}

# Union a deposit routes to when the player has never picked one. Keeps a title
# that carries both tokens (``RT AT`` / ``CC AT``) from silently changing club.
_HOME_UNION_BY_CLUB_NAME: dict[str, str] = {
    ROUND_TABLE_CLUB_NAME.lower(): "RT",
    CREATOR_CLUB_CLUB_NAME.lower(): "CC",
}

_UNION_BY_SHORTHAND: dict[str, RoundTableUnion] = {
    u["shorthand"]: u
    for unions in _UNIONS_BY_CLUB_NAME.values()
    for u in unions
}

def _club_name_key(club_id: int) -> str:
    club = get_club_by_id(club_id)
    name = (club.name if club else None) or ""
    return name.strip().lower()


def _name_key(club_name: str | None) -> str:
    return (club_name or "").strip().lower()


def is_round_table_club(club_id: int) -> bool:
    return _club_name_key(club_id) == ROUND_TABLE_CLUB_NAME.lower()


def is_creator_club(club_id: int) -> bool:
    return _club_name_key(club_id) == CREATOR_CLUB_CLUB_NAME.lower()


def deposit_unions_for_club(club_id: int) -> Optional[tuple[RoundTableUnion, ...]]:
    """Union choices to offer this club, or None when the club has none."""
    return _UNIONS_BY_CLUB_NAME.get(_club_name_key(club_id))


def deposit_unions_for_club_name(
    club_name: str | None,
) -> Optional[tuple[RoundTableUnion, ...]]:
    return _UNIONS_BY_CLUB_NAME.get(_name_key(club_name))


def union_shorthands_for_club(club_id: int) -> frozenset[str]:
    unions = deposit_unions_for_club(club_id)
    return frozenset(u["shorthand"] for u in unions) if unions else frozenset()


def union_shorthands_for_club_name(club_name: str | None) -> frozenset[str]:
    unions = deposit_unions_for_club_name(club_name)
    return frozenset(u["shorthand"] for u in unions) if unions else frozenset()


def home_union_for_club_name(club_name: str | None) -> Optional[str]:
    """Default union shorthand for a club, or None when the club has no unions."""
    return _HOME_UNION_BY_CLUB_NAME.get(_name_key(club_name))


def union_label_for_shorthand(shorthand: str) -> str | None:
    u = _UNION_BY_SHORTHAND.get((shorthand or "").strip().upper())
    return u["label"] if u else None
