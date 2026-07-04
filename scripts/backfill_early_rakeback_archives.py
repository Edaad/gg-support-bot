#!/usr/bin/env python3
"""Backfill early-rakeback Postgres snapshots from aon-beta reset archives.

Usage:
    DATABASE_URL=... AON_BETA_BASE_URL=... AON_BETA_INTERNAL_API_KEY=... \\
        python scripts/backfill_early_rakeback_archives.py

    python scripts/backfill_early_rakeback_archives.py --from-date 2026-06-01 --to-date 2026-07-03
    python scripts/backfill_early_rakeback_archives.py --club-slug clubgto --apply
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from db.connection import get_session, init_engine
from api.early_rakeback_sync import backfill_early_rakeback_from_archives


def _parse_date(raw: str) -> date:
    return date.fromisoformat(raw.strip()[:10])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill early rakeback snapshots from aon-beta archives"
    )
    parser.add_argument(
        "--from-date",
        help="Only audit days on/after YYYY-MM-DD (optional)",
    )
    parser.add_argument(
        "--to-date",
        help="Only audit days on/before YYYY-MM-DD (optional)",
    )
    parser.add_argument(
        "--club-slug",
        action="append",
        dest="club_slugs",
        help="Limit to club slug (repeatable)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write snapshots to Postgres (default is dry-run)",
    )
    args = parser.parse_args()

    from_date = _parse_date(args.from_date) if args.from_date else None
    to_date = _parse_date(args.to_date) if args.to_date else None

    init_engine()
    with get_session() as session:
        reports = backfill_early_rakeback_from_archives(
            session,
            club_slugs=args.club_slugs,
            from_date=from_date,
            to_date=to_date,
        )

        if not reports:
            print("No archived early-rakeback records matched the filters.")
            return 0

        for report in reports:
            print(f"\n=== audit_date {report.audit_date} ===")
            for club in report.clubs:
                print(
                    f"  {club.club_slug}: stored={club.lines_stored} "
                    f"fetched={club.lines_fetched} skipped={club.lines_skipped_unmapped}"
                    + (f" error={club.error}" if club.error else "")
                )
            for warning in report.warnings:
                print(f"  warning: {warning}")

        total_stored = sum(r.total_lines_stored for r in reports)
        print(f"\nTotal lines stored across {len(reports)} audit day(s): {total_stored}")

        if args.apply:
            session.commit()
            print("Committed.")
        else:
            session.rollback()
            print("Dry run — re-run with --apply to commit.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
