"""Deposit/cashout unions per club and club detection.

Round Table players choose between Round Table (TMT) and Aces Table (Massiv).
Creator Club players get the same choice between Creator Club (TMT) and Aces
Table (Massiv); their first Aces Table deposit is gated behind a one-time
"join the club" link (see ``bot/handlers/deposit.py``).

The union only decides **which ClubGG club the chips land in** — payment methods
always come from the dashboard club.
"""

from __future__ import annotations

import logging
from typing import Optional, TypedDict

from bot.services.club import (
    count_deposits_for_chat,
    get_aces_option_min_deposits,
    get_club_by_id,
    get_group_title_for_chat,
    has_aces_join_ack,
)

logger = logging.getLogger(__name__)

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


def has_aces_deposit_history(chat_id: int) -> bool:
    """True when this group already deposits to Aces Table.

    Either the player acknowledged the join link, or the group title already
    carries the ``AT`` tag (groups renamed before the ack column existed).
    """
    try:
        if has_aces_join_ack(int(chat_id)):
            return True
        # Imported here: player_details imports this module, so a module-level
        # import would be circular.
        from bot.services.player_details import parse_group_title_parts

        title, _club_id = get_group_title_for_chat(int(chat_id))
        parsed = parse_group_title_parts(title)
        return bool(parsed and ACES_TABLE_SHORTHAND in parsed.shorthands)
    except Exception:
        logger.exception("aces history check failed chat_id=%s", chat_id)
        return False


def deposit_unions_for_chat(
    club_id: int, chat_id: int | None
) -> Optional[tuple[RoundTableUnion, ...]]:
    """Union choices to offer this group on ``/deposit``.

    Creator Club can hide the picker until the group has enough deposits
    (``clubs.aces_option_min_deposits``). A group that already deposits to Aces
    keeps the picker regardless, so raising the threshold can never silently
    reroute their chips to Creator Club.
    """
    unions = deposit_unions_for_club(club_id)
    if not unions or chat_id is None or not is_creator_club(club_id):
        return unions
    if has_aces_deposit_history(int(chat_id)):
        return unions
    threshold = get_aces_option_min_deposits(club_id)
    if threshold <= 0:
        return unions
    if count_deposits_for_chat(int(chat_id)) >= threshold:
        return unions
    return None


def cashout_unions_for_chat(
    club_id: int, chat_id: int | None
) -> Optional[tuple[RoundTableUnion, ...]]:
    """Union choices to offer this group on the automated ``/cashout``.

    Creator Club only offers Aces Table once the player actually deposits there —
    picking a club they hold no chips in would just fail the claim and escalate.
    """
    unions = deposit_unions_for_club(club_id)
    if not unions or not is_creator_club(club_id):
        return unions
    if chat_id is not None and has_aces_deposit_history(int(chat_id)):
        return unions
    return None


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
