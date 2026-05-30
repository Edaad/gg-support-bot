#!/usr/bin/env python3
"""Backfill Postgres player_details.gg_nickname from gg-computer for all clubs (or one).

Usage:
    # All weekly clubs (clubgto, round-table, aces-table, creator-club)
    DATABASE_URL=... GG_COMPUTER_BASE_URL=... python scripts/backfill_player_details_gg_nickname.py

    # Single club
    DATABASE_URL=... python scripts/backfill_player_details_gg_nickname.py --club-slug aces-table

    # Preview clubs only
    python scripts/backfill_player_details_gg_nickname.py --dry-run

Requires GG_COMPUTER_BASE_URL (or VITE_WEEKLY_STATS_BASE_URL).
"""

from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dotenv import load_dotenv

load_dotenv()

from api.club_slug import ALL_GG_COMPUTER_CLUB_SLUGS, resolve_club_id
from bot.services.gg_computer import gg_computer_base_url
from bot.services.player_details_nickname import refresh_nicknames_for_club
from db.connection import get_db, init_engine


def backfill_club(slug: str) -> dict:
    """Run batch nickname sync for one gg-computer clubId slug."""
    with get_db() as session:
        club_id = resolve_club_id(session, slug)
    return refresh_nicknames_for_club(club_id=club_id, club_slug=slug)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill player_details.gg_nickname from gg-computer for all weekly clubs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--club-slug",
        action="append",
        dest="club_slugs",
        metavar="SLUG",
        help=f"gg-computer slug (repeatable). Default: all ({', '.join(ALL_GG_COMPUTER_CLUB_SLUGS)}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List clubs that would be processed; no DB or HTTP calls.",
    )
    args = parser.parse_args()
    slugs = tuple(args.club_slugs) if args.club_slugs else ALL_GG_COMPUTER_CLUB_SLUGS

    if args.dry_run:
        for slug in slugs:
            print(f"would backfill: {slug}")
        return 0

    if not gg_computer_base_url():
        print(
            "error: set GG_COMPUTER_BASE_URL (or VITE_WEEKLY_STATS_BASE_URL)",
            file=sys.stderr,
        )
        return 1

    if not os.getenv("DATABASE_URL"):
        print("error: set DATABASE_URL", file=sys.stderr)
        return 1

    init_engine()

    total_updated = 0
    total_missing = 0
    failures = 0

    for slug in slugs:
        try:
            result = backfill_club(slug)
        except Exception as exc:
            failures += 1
            print(f"[{slug}] FAILED: {exc}")
            continue

        err = result.get("error")
        updated = int(result.get("updated", 0))
        missing = int(result.get("missing", 0))
        skipped = int(result.get("skipped", 0))
        total_updated += updated
        total_missing += missing

        if err:
            failures += 1
            print(f"[{slug}] error={err} updated={updated} missing={missing} skipped={skipped}")
        else:
            print(f"[{slug}] ok updated={updated} missing={missing} skipped={skipped}")

    print(
        f"\nDone: clubs={len(slugs)} updated={total_updated} missing={total_missing} failures={failures}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
