"""Direct Mongo reads for gg-computer player_details (fallback when API route missing)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, List, Optional

WEEKLY_CLUB_SLUGS = frozenset(
    {"round-table", "aces-table", "creator-club", "clubgto"}
)

LEGACY_CLUB_TO_WEEKLY = {
    "aces-tables": "aces-table",
}


def gg_computer_mongodb_uri() -> Optional[str]:
    for key in ("GG_COMPUTER_MONGODB_URI", "MONGODB_URI"):
        raw = os.getenv(key)
        if raw and str(raw).strip():
            return str(raw).strip()
    return None


def normalize_weekly_club_slug(club_slug: str) -> Optional[str]:
    slug = (club_slug or "").strip().lower()
    if slug in WEEKLY_CLUB_SLUGS:
        return slug
    return None


def legacy_club_to_weekly_slug(legacy_club: str) -> Optional[str]:
    raw = (legacy_club or "").strip()
    if not raw:
        return None
    if raw in WEEKLY_CLUB_SLUGS:
        return raw
    return LEGACY_CLUB_TO_WEEKLY.get(raw)


def player_details_club_filter(club_slug: str) -> dict[str, Any]:
    slug = normalize_weekly_club_slug(club_slug)
    if not slug:
        raise ValueError(f"Unknown club slug: {club_slug!r}")
    return {
        "$or": [
            {"clubId": slug},
            {"clubId": {"$exists": False}, "clubs": slug},
        ]
    }


def _to_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def format_player_details_row(doc: dict[str, Any], club_slug: str) -> Optional[dict[str, Any]]:
    """Normalize one Mongo player_details doc for a target weekly club slug."""
    gg_id = doc.get("gg_id")
    if not isinstance(gg_id, str) or not gg_id.strip():
        return None
    gg_id = gg_id.strip()

    club_id = doc.get("clubId")
    if isinstance(club_id, str) and club_id.strip():
        if club_id.strip() != club_slug:
            return None
    else:
        legacy_clubs = doc.get("clubs") if isinstance(doc.get("clubs"), list) else []
        slugs = {
            s
            for c in legacy_clubs
            if isinstance(c, str)
            for s in [legacy_club_to_weekly_slug(c)]
            if s
        }
        if club_slug not in slugs:
            return None

    nickname = doc.get("nickname")
    nick = nickname.strip() if isinstance(nickname, str) else ""
    agent = doc.get("agent")
    return {
        "gg_id": gg_id,
        "nickname": nick,
        "agent": agent.strip() if isinstance(agent, str) and agent.strip() else None,
        "updated_at": _to_iso(doc.get("updated_at")),
    }


def list_player_details_rows_for_club(docs: list[dict[str, Any]], club_slug: str) -> List[dict[str, Any]]:
    slug = normalize_weekly_club_slug(club_slug)
    if not slug:
        raise ValueError(f"Unknown club slug: {club_slug!r}")
    seen: set[str] = set()
    players: list[dict[str, Any]] = []
    for doc in docs:
        row = format_player_details_row(doc, slug)
        if not row or row["gg_id"] in seen:
            continue
        seen.add(row["gg_id"])
        players.append(row)
    players.sort(key=lambda r: r["gg_id"])
    return players


def list_player_details_from_mongo(club_slug: str) -> List[dict[str, Any]]:
    """Read gg-computer Mongo player_details for one weekly club slug."""
    uri = gg_computer_mongodb_uri()
    if not uri:
        raise ValueError("mongo_not_configured")

    try:
        from pymongo import MongoClient
    except ImportError as exc:
        raise ValueError("pymongo_not_installed") from exc

    slug = normalize_weekly_club_slug(club_slug)
    if not slug:
        raise ValueError(f"Unknown club slug: {club_slug!r}")

    db_name = os.getenv("GG_COMPUTER_DATABASE_NAME") or os.getenv("DATABASE_NAME")
    client = MongoClient(uri, serverSelectionTimeoutMS=10_000)
    try:
        if db_name and str(db_name).strip():
            db = client[str(db_name).strip()]
        else:
            db = client.get_default_database()
        coll = db["player_details"]
        docs = list(
            coll.find(
                player_details_club_filter(slug),
                {"gg_id": 1, "nickname": 1, "agent": 1, "updated_at": 1, "clubId": 1, "clubs": 1},
            )
        )
    finally:
        client.close()

    return list_player_details_rows_for_club(docs, slug)
