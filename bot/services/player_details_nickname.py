"""Sync gg_nickname on Postgres player_details from gg-computer Mongo player_details."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from api.club_slug import slug_for_club_id as _slug_for_club_id_session
from bot.services.gg_computer import (
    batch_player_details,
    fetch_player_details,
    gg_computer_base_url,
    nickname_from_player_details_payload,
)
from db.connection import get_db

logger = logging.getLogger(__name__)


def slug_for_club_id(club_id: int) -> Optional[str]:
    with get_db() as session:
        return _slug_for_club_id_session(session, club_id)


def set_gg_nickname(*, club_id: int, gg_player_id: str, nickname: Optional[str]) -> None:
    stmt = text(
        """
        UPDATE player_details
        SET gg_nickname = :nickname
        WHERE club_id = :club_id AND gg_player_id = :gg_player_id
        """
    )
    nick = (nickname or "").strip() or None
    with get_db() as session:
        session.execute(
            stmt,
            {
                "club_id": int(club_id),
                "gg_player_id": gg_player_id.strip(),
                "nickname": nick,
            },
        )


def refresh_nickname_for_player(
    *,
    club_id: int,
    gg_player_id: str,
    club_slug: Optional[str] = None,
) -> bool:
    """Fetch nickname from gg-computer and update Postgres. Returns True if updated."""
    slug = club_slug or slug_for_club_id(club_id)
    if not slug:
        return False
    if not gg_computer_base_url():
        return False
    try:
        data = fetch_player_details(gg_player_id, club_slug=slug)
    except ValueError:
        return False
    if not data:
        return False
    nick = nickname_from_player_details_payload(data)
    if not nick:
        return False
    set_gg_nickname(club_id=club_id, gg_player_id=gg_player_id, nickname=nick)
    return True


def refresh_nicknames_for_club(
    *,
    club_id: int,
    club_slug: Optional[str] = None,
) -> Dict[str, Any]:
    """Batch-fetch nicknames for all player_details rows in a club."""
    slug = club_slug or slug_for_club_id(club_id)
    if not slug:
        return {
            "updated": 0,
            "missing": 0,
            "skipped": 0,
            "error": "no_club_slug",
        }
    if not gg_computer_base_url():
        return {
            "updated": 0,
            "missing": 0,
            "skipped": 0,
            "error": "gg_computer_not_configured",
        }

    stmt = text(
        """
        SELECT gg_player_id
        FROM player_details
        WHERE club_id = :club_id
        ORDER BY gg_player_id
        """
    )
    with get_db() as session:
        rows = session.execute(stmt, {"club_id": int(club_id)}).fetchall()
    gg_ids = [str(r[0]) for r in rows if r and r[0]]
    if not gg_ids:
        return {"updated": 0, "missing": 0, "skipped": 0}

    try:
        found, missing = batch_player_details(slug, gg_ids)
    except Exception as exc:
        logger.warning("gg-computer batch player-details failed: %s", exc)
        return {
            "updated": 0,
            "missing": 0,
            "skipped": len(gg_ids),
            "error": str(exc),
        }

    updated = 0
    for row in found:
        gid = row.get("gg_id")
        nick = row.get("nickname")
        if not isinstance(gid, str) or not isinstance(nick, str) or not nick.strip():
            continue
        set_gg_nickname(club_id=club_id, gg_player_id=gid, nickname=nick.strip())
        updated += 1

    return {
        "updated": updated,
        "missing": len(missing),
        "skipped": max(0, len(gg_ids) - updated - len(missing)),
        "club_slug": slug,
    }


def try_refresh_nickname_after_bind(*, club_id: int, gg_player_id: str) -> None:
    """Best-effort nickname sync; never raises."""
    try:
        refresh_nickname_for_player(club_id=club_id, gg_player_id=gg_player_id)
    except Exception as exc:
        logger.debug("nickname refresh after bind skipped: %s", exc)
