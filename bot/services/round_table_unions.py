"""Round Table deposit unions (TMT / Massiv) and club detection."""

from __future__ import annotations

from typing import TypedDict

from bot.services.club import get_club_by_id

ROUND_TABLE_CLUB_NAME = "Round Table"
ROUND_TABLE_UNION_SHORTHANDS = frozenset({"RT", "AT"})


class RoundTableUnion(TypedDict):
    shorthand: str
    label: str


ROUND_TABLE_DEPOSIT_UNIONS: tuple[RoundTableUnion, ...] = (
    {"shorthand": "RT", "label": "Round Table (TMT Union)"},
    {"shorthand": "AT", "label": "Aces Table (Massiv Union)"},
)

_UNION_BY_SHORTHAND = {u["shorthand"]: u for u in ROUND_TABLE_DEPOSIT_UNIONS}


def is_round_table_club(club_id: int) -> bool:
    club = get_club_by_id(club_id)
    return bool(club and (club.name or "").strip().lower() == ROUND_TABLE_CLUB_NAME.lower())


def union_label_for_shorthand(shorthand: str) -> str | None:
    u = _UNION_BY_SHORTHAND.get((shorthand or "").strip().upper())
    return u["label"] if u else None
