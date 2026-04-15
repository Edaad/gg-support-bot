"""Helpers for binding a Telegram group chat to GG player ids via group title parsing.

Title convention:
    SHORTHAND / GGPLAYERID / anything
Example:
    GTO / 8190-5287 / ThePirate343
"""

from __future__ import annotations

import re
from typing import Optional, Tuple, List

from sqlalchemy import text

from config import CLUB_SHORTHAND_TO_NAME
from db.connection import get_db
from db.models import Club

_GG_RE = re.compile(r"^[0-9]{1,48}-[0-9]{1,48}$")


def parse_tracking_title(title: str | None) -> Optional[Tuple[str, str]]:
    """Return (shorthand, gg_player_id) if the title matches, else None."""
    if not title:
        return None
    parts = [p.strip() for p in title.split("/") if p.strip()]
    if len(parts) < 2:
        return None
    shorthand = parts[0].upper()
    gg_player_id = parts[1]
    if not shorthand or not _GG_RE.match(gg_player_id):
        return None
    return shorthand, gg_player_id


def resolve_club_id_from_shorthand(shorthand: str) -> Optional[int]:
    """Map shorthand to clubs.name then resolve to clubs.id (case-insensitive exact match)."""
    full_name = CLUB_SHORTHAND_TO_NAME.get(shorthand.upper())
    if not full_name:
        return None
    with get_db() as session:
        club = (
            session.query(Club)
            .filter(text("lower(name) = lower(:n)"))
            .params(n=full_name)
            .first()
        )
        return int(club.id) if club else None


def bind_chat_from_title(*, chat_id: int, title: str | None) -> Optional[str]:
    """Parse title, resolve club, and bind. Returns gg_player_id on success, else None."""
    parsed = parse_tracking_title(title)
    if not parsed:
        return None
    shorthand, gg_player_id = parsed
    club_id = resolve_club_id_from_shorthand(shorthand)
    if not club_id:
        return None
    bind_chat_to_player(club_id=club_id, gg_player_id=gg_player_id, chat_id=chat_id)
    return gg_player_id


def bind_chat_to_player(*, club_id: int, gg_player_id: str, chat_id: int) -> None:
    """Upsert (gg_player_id, club_id) and merge chat_id into chat_ids distinct."""
    stmt = text(
        """
        INSERT INTO player_details (chat_ids, gg_player_id, club_id)
        VALUES (:chat_ids, :gg_player_id, :club_id)
        ON CONFLICT (gg_player_id, club_id) DO UPDATE SET
            chat_ids = (
                SELECT ARRAY(
                    SELECT DISTINCT unnest(
                        COALESCE(player_details.chat_ids, '{}'::bigint[])
                        || COALESCE(EXCLUDED.chat_ids, '{}'::bigint[])
                    )
                    ORDER BY 1
                )
            )
        """
    )
    with get_db() as session:
        session.execute(
            stmt,
            {
                "chat_ids": [int(chat_id)],
                "gg_player_id": gg_player_id,
                "club_id": int(club_id),
            },
        )


def get_bound_players(*, club_id: int, chat_id: int) -> List[str]:
    """Return gg_player_id values for rows whose chat_ids contain chat_id."""
    stmt = text(
        """
        SELECT gg_player_id
        FROM player_details
        WHERE club_id = :club_id
          AND chat_ids @> ARRAY[:chat_id]::bigint[]
        ORDER BY gg_player_id
        """
    )
    with get_db() as session:
        rows = session.execute(
            stmt, {"club_id": int(club_id), "chat_id": int(chat_id)}
        ).fetchall()
        return [r[0] for r in rows if r and r[0]]

