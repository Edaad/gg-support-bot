"""Reset skipped migrated_group_recovery rows back to pending for retry.

Dry-run by default; pass ``--apply`` to write Postgres.

Usage:
  python scripts/reopen_recovery_skipped.py --club clubgto
  python scripts/reopen_recovery_skipped.py --club clubgto --apply
  python scripts/reopen_recovery_skipped.py --club clubgto --row-id 42 --apply
  python scripts/reopen_recovery_skipped.py --club clubgto --apply --limit 1
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass


def _query_rows(
    *,
    club_key: str,
    row_id: int | None,
    limit: int | None,
    all_tiers: bool,
):
    from bot.services.migration_recovery import recovery_priority_tiers_for_club
    from db.connection import get_db, init_engine
    from db.models import MigratedGroupRecovery

    init_engine()
    tiers = None if all_tiers else recovery_priority_tiers_for_club(club_key)
    with get_db() as session:
        q = session.query(MigratedGroupRecovery).filter(
            MigratedGroupRecovery.club_key == club_key,
            MigratedGroupRecovery.readd_status == "skipped",
        )
        if row_id is not None:
            q = q.filter(MigratedGroupRecovery.id == int(row_id))
        if tiers is not None:
            q = q.filter(MigratedGroupRecovery.priority_tier.in_(tiers))
        q = q.order_by(
            MigratedGroupRecovery.priority_tier.asc(),
            MigratedGroupRecovery.priority_rank.asc(),
        )
        if limit is not None:
            q = q.limit(int(limit))
        return q.all()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--club",
        required=True,
        choices=["round_table", "creator_club", "clubgto"],
    )
    parser.add_argument("--row-id", type=int, help="Reopen one row only.")
    parser.add_argument("--limit", type=int, help="Max rows to reopen.")
    parser.add_argument(
        "--all-tiers",
        action="store_true",
        help="Include all tiers (default: club recovery tier scope only).",
    )
    parser.add_argument("--apply", action="store_true", help="Write changes to Postgres.")
    args = parser.parse_args()

    rows = _query_rows(
        club_key=args.club,
        row_id=args.row_id,
        limit=args.limit,
        all_tiers=bool(args.all_tiers),
    )
    if not rows:
        print("No matching skipped rows.")
        return

    err_counts = Counter((row.last_error or "(none)") for row in rows)
    tier_counts = Counter(int(row.priority_tier) for row in rows)
    print(f"Matched {len(rows)} skipped row(s) for club={args.club}")
    print("By tier:", dict(sorted(tier_counts.items())))
    print("By last_error:")
    for err, count in err_counts.most_common():
        print(f"  {count}x {err}")

    if not args.apply:
        print("\nDry-run only. Pass --apply to reset these rows to pending.")
        for row in rows[:10]:
            print(
                f"  id={row.id} tier={row.priority_tier} chat={row.telegram_chat_id} "
                f"title={row.group_title[:50]!r} last_error={row.last_error!r}"
            )
        if len(rows) > 10:
            print(f"  ... and {len(rows) - 10} more")
        return

    from db.connection import get_db, init_engine

    init_engine()
    now = datetime.now(timezone.utc)
    updated = 0
    with get_db() as session:
        for row in rows:
            db_row = session.get(type(row), int(row.id))
            if db_row is None:
                continue
            prior_error = db_row.last_error
            db_row.readd_status = "pending"
            db_row.last_error = None
            db_row.readd_attempted_at = None
            db_row.readd_completed_at = None
            db_row.readd_result = {
                "reopened_from_skipped": True,
                "prior_last_error": prior_error,
                "reopened_at": now.isoformat(),
            }
            db_row.updated_at = now
            updated += 1
        session.commit()
    print(f"\nReopened {updated} row(s) to pending.")


if __name__ == "__main__":
    main()
