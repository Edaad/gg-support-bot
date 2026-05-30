"""gg-computer club slugs ↔ canonical Postgres clubs.name (see dashboard/src/config/clubMap.ts)."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from db.models import Club

# Slug -> clubs.name (must match dashboard CLUB_OPTIONS labels where applicable)
CLUB_SLUG_TO_NAME: dict[str, str] = {
    "clubgto": "ClubGTO",
    "round-table": "Round Table",
    "aces-table": "Round Table",
    "creator-club": "Creator Club",
}

# gg-computer weekly slugs (dashboard/src/config/clubMap.ts); use for all-club backfills
ALL_GG_COMPUTER_CLUB_SLUGS: tuple[str, ...] = (
    "clubgto",
    "round-table",
    "aces-table",
    "creator-club",
)

# Explicit labels for clubs whose DB name differs from CLUB_SLUG_TO_NAME value
CLUB_LABEL_TO_SLUG: dict[str, str] = {
    "clubgto": "clubgto",
    "round table": "round-table",
    "aces table": "aces-table",
    "creator club": "creator-club",
}


def slug_for_club_name(name: str) -> str | None:
    """Map Postgres clubs.name (or display label) to gg-computer clubId slug."""
    raw = (name or "").strip()
    if not raw:
        return None
    key = raw.lower()
    if key in CLUB_LABEL_TO_SLUG:
        return CLUB_LABEL_TO_SLUG[key]
    matches = [
        slug
        for slug, full in CLUB_SLUG_TO_NAME.items()
        if full.strip().lower() == key
    ]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    if "round-table" in matches:
        return "round-table"
    return matches[0]


def resolve_club_id(db: Session, club_slug: str) -> int:
    key = club_slug.strip().lower()
    full_name = CLUB_SLUG_TO_NAME.get(key)
    if not full_name:
        raise HTTPException(400, f"Unknown club slug: {club_slug!r}")
    club = (
        db.query(Club)
        .filter(text("lower(name) = lower(:n)"))
        .params(n=full_name)
        .first()
    )
    if not club:
        raise HTTPException(
            404,
            f"No club named {full_name!r} in database — check clubs.name matches mapping.",
        )
    return int(club.id)


def slug_for_club_id(db: Session, club_id: int) -> str | None:
    club = db.query(Club).filter(Club.id == int(club_id)).first()
    if not club or not club.name:
        return None
    return slug_for_club_name(str(club.name))
