"""Helpers for binding a Telegram group chat to GG player ids via group title parsing.

Title convention:
    SHORTHAND / GGPLAYERID / anything
    SHORTHAND may be combined for Round Table unions: RT AT / GGPLAYERID / anything
Example:
    GTO / 8190-5287 / ThePirate343
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional, Tuple, List, Set

from sqlalchemy import text

from config import CLUB_SHORTHAND_TO_NAME
from bot.services.round_table_unions import ROUND_TABLE_UNION_SHORTHANDS
from db.connection import get_db
from db.models import Club

_GG_RE = re.compile(r"^[0-9]{1,48}-[0-9]{1,48}$")


@dataclass(frozen=True)
class BindResult:
    ok: bool
    gg_player_id: Optional[str] = None
    club_id: Optional[int] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class OverrideResult:
    ok: bool
    gg_player_id: Optional[str] = None
    club_id: Optional[int] = None
    previous_chat_ids: Tuple[int, ...] = ()
    error: Optional[str] = None


@dataclass(frozen=True)
class GroupTitleParts:
    shorthands: frozenset[str]
    gg_player_id: str
    tail: str


def _shorthands_from_prefix_segment(segment: str) -> Set[str]:
    return {
        token.upper()
        for token in (segment or "").split()
        if token.upper() in CLUB_SHORTHAND_TO_NAME
    }


def format_title_prefix_segment(shorthands: Set[str]) -> str:
    """Render first title segment from known shorthand tokens."""
    s = {x.upper() for x in shorthands if x.upper() in CLUB_SHORTHAND_TO_NAME}
    if not s:
        return ""
    if "RT" in s and "AT" in s:
        return "RT AT"
    if len(s) == 1:
        return next(iter(s))
    return " ".join(sorted(s))


def parse_group_title_parts(title: str | None) -> Optional[GroupTitleParts]:
    """Parse group title into shorthand set, gg_player_id, and trailing label."""
    if not title:
        return None
    parts = [p.strip() for p in title.split("/") if p.strip()]
    if len(parts) < 2:
        return None
    gg_player_id = parts[1]
    if not _GG_RE.match(gg_player_id):
        return None
    shorthands = _shorthands_from_prefix_segment(parts[0])
    if not shorthands:
        return None
    tail = " / ".join(parts[2:]).strip() if len(parts) > 2 else ""
    return GroupTitleParts(
        shorthands=frozenset(shorthands),
        gg_player_id=gg_player_id,
        tail=tail,
    )


def merge_union_prefix(current_title: str | None, chosen_shorthand: str) -> Optional[str]:
    """Return new group title if deposit union shorthand should be merged into prefix."""
    chosen = (chosen_shorthand or "").strip().upper()
    if chosen not in ROUND_TABLE_UNION_SHORTHANDS:
        return None
    parsed = parse_group_title_parts(current_title)
    if not parsed:
        return None
    if chosen in parsed.shorthands:
        return None
    merged = set(parsed.shorthands) | {chosen}
    prefix = format_title_prefix_segment(merged)
    if parsed.tail:
        return f"{prefix} / {parsed.gg_player_id} / {parsed.tail}"
    return f"{prefix} / {parsed.gg_player_id}"


def gg_player_id_from_title(title: str | None) -> Optional[str]:
    """Return the GG player id segment from a group title, if present."""
    parsed = parse_group_title_parts(title)
    return parsed.gg_player_id if parsed else None


def parse_tracking_title(title: str | None) -> Optional[Tuple[str, str]]:
    """Return (shorthand, gg_player_id) if the title matches, else None."""
    parsed = parse_group_title_parts(title)
    if not parsed:
        return None
    prefix = format_title_prefix_segment(set(parsed.shorthands))
    if not prefix:
        return None
    return prefix, parsed.gg_player_id


def resolve_club_id_from_shorthand(shorthand: str) -> Optional[int]:
    """Map shorthand to clubs.name then resolve to clubs.id (case-insensitive exact match)."""
    tokens = [t for t in (shorthand or "").upper().split() if t]
    if not tokens and shorthand:
        tokens = [shorthand.upper()]
    seen_names: set[str] = set()
    for token in tokens:
        full_name = CLUB_SHORTHAND_TO_NAME.get(token)
        if not full_name or full_name in seen_names:
            continue
        seen_names.add(full_name)
        with get_db() as session:
            club = (
                session.query(Club)
                .filter(text("lower(name) = lower(:n)"))
                .params(n=full_name)
                .first()
            )
            if club:
                return int(club.id)
    return None


def get_existing_chat_ids(*, club_id: int, gg_player_id: str) -> Optional[List[int]]:
    """Return chat_ids array for an existing (club_id, gg_player_id) row, or None if not present."""
    stmt = text(
        """
        SELECT chat_ids
        FROM player_details
        WHERE club_id = :club_id
          AND gg_player_id = :gg_player_id
        LIMIT 1
        """
    )
    with get_db() as session:
        row = session.execute(
            stmt, {"club_id": int(club_id), "gg_player_id": gg_player_id}
        ).fetchone()
        if not row:
            return None
        chat_ids = row[0] or []
        try:
            return [int(x) for x in chat_ids]
        except Exception:
            return []


def check_same_club_player_conflict(
    *, club_id: int, gg_player_id: str, chat_id: int
) -> Optional[str]:
    """If gg_player_id is already tracked by other chat(s) in the same club, return an error string."""
    existing = get_existing_chat_ids(club_id=club_id, gg_player_id=gg_player_id)
    if not existing:
        return None
    others = [c for c in existing if int(c) != int(chat_id)]
    if not others:
        return None
    conflict_prefix = "Oops! Seems like the player with PLAYER ID:"
    if len(others) == 1:
        return (
            f"{conflict_prefix} {gg_player_id} already has another group chat for this club."
        )
    return (
        f"{conflict_prefix} {gg_player_id} already has other group chats "
        f"for this club ({len(others)} linked chats)."
    )


def is_same_club_player_conflict_message(msg: Optional[str]) -> bool:
    """True if error text was produced by check_same_club_player_conflict (for handler routing)."""
    if not msg:
        return False
    return msg.startswith("Oops! Seems like the player with PLAYER ID:")


def bind_chat_from_title(*, chat_id: int, title: str | None) -> BindResult:
    """Parse title, resolve club, and bind (with same-club conflict checks)."""
    parsed = parse_tracking_title(title)
    if not parsed:
        return BindResult(ok=False, error="Invalid group name format.")
    shorthand, gg_player_id = parsed
    club_id = resolve_club_id_from_shorthand(shorthand)
    if not club_id:
        return BindResult(ok=False, gg_player_id=gg_player_id, error="Unknown club shorthand.")

    conflict = check_same_club_player_conflict(
        club_id=club_id, gg_player_id=gg_player_id, chat_id=chat_id
    )
    if conflict:
        return BindResult(
            ok=False,
            gg_player_id=gg_player_id,
            club_id=club_id,
            error=conflict,
        )
    bind_chat_to_player(club_id=club_id, gg_player_id=gg_player_id, chat_id=chat_id)
    return BindResult(ok=True, gg_player_id=gg_player_id, club_id=club_id)


def override_chat_for_player(*, club_id: int, gg_player_id: str, chat_id: int) -> OverrideResult:
    """Make *chat_id* the only tracked group for this player in the club (replaces other chats)."""
    if not _GG_RE.match(gg_player_id):
        return OverrideResult(ok=False, error="Invalid player id format (example: 1111-2222).")
    cid = int(chat_id)
    club = int(club_id)
    previous = get_existing_chat_ids(club_id=club, gg_player_id=gg_player_id) or []
    previous_other = tuple(c for c in previous if int(c) != cid)

    remove_stmt = text(
        """
        UPDATE player_details
        SET chat_ids = COALESCE(
            (
                SELECT ARRAY(
                    SELECT x
                    FROM unnest(chat_ids) AS x
                    WHERE x <> :chat_id
                    ORDER BY 1
                )
            ),
            '{}'::bigint[]
        )
        WHERE club_id = :club_id
          AND chat_ids @> ARRAY[:chat_id]::bigint[]
        """
    )
    set_stmt = text(
        """
        INSERT INTO player_details (chat_ids, gg_player_id, club_id)
        VALUES (ARRAY[:chat_id]::bigint[], :gg_player_id, :club_id)
        ON CONFLICT (gg_player_id, club_id) DO UPDATE SET
            chat_ids = ARRAY[:chat_id]::bigint[]
        """
    )
    with get_db() as session:
        session.execute(remove_stmt, {"chat_id": cid, "club_id": club})
        session.execute(
            set_stmt,
            {"chat_id": cid, "gg_player_id": gg_player_id, "club_id": club},
        )
    return OverrideResult(
        ok=True,
        gg_player_id=gg_player_id,
        club_id=club,
        previous_chat_ids=previous_other,
    )


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

