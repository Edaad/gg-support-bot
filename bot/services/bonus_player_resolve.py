"""Resolve bonus player identity from Telegram group title → player_details."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text

from bot.services.player_details import (
    bind_chat_to_player,
    format_title_prefix_segment,
    parse_group_title_parts,
    parse_tracking_title,
    resolve_club_id_from_shorthand,
)
from cashier.services.zapier import build_zapier_name
from db.connection import get_db

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BonusPlayerContext:
    group_title: str
    gg_player_id: str
    club_id: int
    chat_id: int | None
    player_details_id: int | None
    zapier_name: str


def _lookup_player_details_id(*, club_id: int, gg_player_id: str) -> int | None:
    stmt = text(
        """
        SELECT id
        FROM player_details
        WHERE club_id = :club_id AND gg_player_id = :gg_player_id
        LIMIT 1
        """
    )
    with get_db() as session:
        row = session.execute(
            stmt,
            {"club_id": int(club_id), "gg_player_id": gg_player_id.strip()},
        ).fetchone()
    if not row:
        return None
    return int(row[0])


def resolve_bonus_player(
    *,
    group_title: str,
    chat_id: int | None = None,
    club_id: int | None = None,
) -> BonusPlayerContext | None:
    """Parse group title, bind chat when known, sync nickname, return context or None."""
    title = (group_title or "").strip()
    if not title:
        return None

    parsed = parse_group_title_parts(title)
    if not parsed:
        return None

    tracking = parse_tracking_title(title)
    if not tracking:
        return None
    shorthand, gg_player_id = tracking

    resolved_club_id = resolve_club_id_from_shorthand(shorthand)
    if resolved_club_id is None:
        return None

    if club_id is not None and int(club_id) != int(resolved_club_id):
        logger.warning(
            "bonus resolve: club_id mismatch context=%s title=%s resolved=%s",
            club_id,
            title,
            resolved_club_id,
        )
        return None

    cid = int(resolved_club_id)
    chat_id_int = int(chat_id) if chat_id is not None else None

    if chat_id_int is not None:
        bind_chat_to_player(club_id=cid, gg_player_id=gg_player_id, chat_id=chat_id_int)
        from bot.services.player_details_nickname import try_refresh_nickname_after_bind

        try_refresh_nickname_after_bind(club_id=cid, gg_player_id=gg_player_id)

    zapier_name = build_zapier_name(title)
    if not zapier_name:
        prefix = format_title_prefix_segment(set(parsed.shorthands))
        tail = parsed.tail or ""
        if tail:
            zapier_name = f"{prefix} / {gg_player_id} / {tail}"
        else:
            zapier_name = f"{prefix} / {gg_player_id}"

    player_details_id = _lookup_player_details_id(club_id=cid, gg_player_id=gg_player_id)

    return BonusPlayerContext(
        group_title=title,
        gg_player_id=gg_player_id,
        club_id=cid,
        chat_id=chat_id_int,
        player_details_id=player_details_id,
        zapier_name=zapier_name,
    )
