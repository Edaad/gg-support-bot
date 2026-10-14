"""Seed contact_restore_rows from the August MTProto contact export.

Phone numbers in the CSV are ignored and are not written.

Usage:
  python scripts/seed_contact_restore.py
  python scripts/seed_contact_restore.py --club-key round_table
  python scripts/seed_contact_restore.py --apply
  python scripts/seed_contact_restore.py --apply --club-key round_table
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from club_gc_settings import CONTACT_RESTORE_CLUB_KEYS  # noqa: E402
from bot.services.contact_restore import parse_contact_restore_csv  # noqa: E402

_DEFAULT_CSV = _REPO_ROOT / "data" / "mtproto_contacts_export.csv"


def _summarize(rows: list[dict]) -> None:
    by_club: dict[str, Counter] = {}
    for row in rows:
        counts = by_club.setdefault(row["club_key"], Counter())
        counts["csv"] += 1
        if row["status"] == "skipped":
            counts["skipped_empty"] += 1
    for club in CONTACT_RESTORE_CLUB_KEYS:
        counts = by_club.get(club)
        if counts is None:
            continue
        print(
            f"club={club} csv={counts['csv']} skipped_empty={counts['skipped_empty']}",
            flush=True,
        )


def _insert(rows: list[dict]) -> int:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from db.connection import get_db
    from db.models import ContactRestoreRow

    inserted = 0
    with get_db() as session:
        for offset in range(0, len(rows), 500):
            chunk = rows[offset : offset + 500]
            stmt = pg_insert(ContactRestoreRow).values(chunk)
            stmt = stmt.on_conflict_do_nothing(
                constraint="contact_restore_rows_club_key_contact_user_id_key"
            )
            result = session.execute(stmt)
            inserted += int(result.rowcount or 0)
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=_DEFAULT_CSV)
    parser.add_argument("--club-key", choices=CONTACT_RESTORE_CLUB_KEYS)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write rows. Without this flag the script only prints counts.",
    )
    args = parser.parse_args()
    if not args.csv.is_file():
        print(f"csv not found: {args.csv}", file=sys.stderr)
        return 1
    rows = parse_contact_restore_csv(args.csv, club_key=args.club_key)
    _summarize(rows)
    if not args.apply:
        print(f"dry-run rows={len(rows)}", flush=True)
        return 0
    inserted = _insert(rows)
    print(f"applied inserted={inserted} csv_rows={len(rows)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
