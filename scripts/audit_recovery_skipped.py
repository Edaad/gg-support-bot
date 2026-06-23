"""Audit skipped migration recovery rows for resolvable players (MTProto).

Re-runs player discovery on tier-3 (or club recovery scope) skipped rows and
reports which could be retried. Read-only by default.

Usage:
  python scripts/audit_recovery_skipped.py --club clubgto
  python scripts/audit_recovery_skipped.py --club clubgto --row-id 42
  python scripts/audit_recovery_skipped.py --club clubgto --limit 5
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from bot.services.migration_recovery import recovery_priority_tiers_for_club  # noqa: E402
from scripts.triage_recovery_tier3_pending import (  # noqa: E402
    CLUB_KEYS,
    RecoveryRowForTriage,
    _check_accounts_for_club,
    _row_from_orm,
)

CSV_FIELDS = (
    "row_id",
    "club_key",
    "telegram_chat_id",
    "group_title",
    "player_telegram_user_id",
    "player_username",
    "skip_reason",
    "account_check",
    "resolvable",
    "discovered_player_id",
    "discovered_username",
    "discovered_display_name",
)


def _load_skipped_rows(
    *,
    club_filter: str,
    row_id: int | None,
    limit: int | None,
    all_tiers: bool,
) -> list[RecoveryRowForTriage]:
    from db.connection import get_db, init_engine
    from db.models import MigratedGroupRecovery

    init_engine()
    tiers = None if all_tiers else recovery_priority_tiers_for_club(club_filter)
    with get_db() as session:
        q = session.query(MigratedGroupRecovery).filter(
            MigratedGroupRecovery.club_key == club_filter,
            MigratedGroupRecovery.readd_status == "skipped",
        )
        if row_id is not None:
            q = q.filter(MigratedGroupRecovery.id == int(row_id))
        if tiers is not None:
            q = q.filter(MigratedGroupRecovery.priority_tier.in_(tiers))
        q = q.order_by(
            MigratedGroupRecovery.priority_tier.asc(),
            MigratedGroupRecovery.priority_rank.asc(),
            MigratedGroupRecovery.id.asc(),
        )
        if limit is not None:
            q = q.limit(int(limit))
        return [_row_from_orm(r, cohort="tier3_pending") for r in q.all()]


def _default_output_path(club_key: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _REPO_ROOT / "backups" / f"recovery_skipped_audit_{club_key}_{stamp}.csv"


async def run_audit(
    *,
    club_filter: str,
    row_id: int | None,
    limit: int | None,
    all_tiers: bool,
    delay_sec: float,
) -> list[dict[str, Any]]:
    rows = _load_skipped_rows(
        club_filter=club_filter,
        row_id=row_id,
        limit=limit,
        all_tiers=all_tiers,
    )
    if not rows:
        return []

    print(
        f"MTProto player resolution for {len(rows)} skipped row(s) ({club_filter})...",
        flush=True,
    )
    resolutions = await _check_accounts_for_club(
        club_filter,
        rows,
        delay_sec=delay_sec,
    )

    out: list[dict[str, Any]] = []
    for row in rows:
        resolution = resolutions.get(row.row_id)
        resolvable = (
            resolution is not None
            and resolution.account_check == "alive"
            and resolution.user_id is not None
        )
        out.append(
            {
                "row_id": row.row_id,
                "club_key": row.club_key,
                "telegram_chat_id": row.telegram_chat_id,
                "group_title": row.group_title,
                "player_telegram_user_id": row.player_telegram_user_id or "",
                "player_username": row.player_username or "",
                "skip_reason": row.row_last_error or "",
                "account_check": resolution.account_check if resolution else "uncheckable",
                "resolvable": "yes" if resolvable else "no",
                "discovered_player_id": resolution.user_id if resolution else "",
                "discovered_username": resolution.username if resolution else "",
                "discovered_display_name": resolution.display_name if resolution else "",
            }
        )
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _print_summary(rows: list[dict[str, Any]]) -> None:
    resolvable = [r for r in rows if r["resolvable"] == "yes"]
    print(f"\nTotal skipped audited: {len(rows)}")
    print(f"Resolvable (alive player found): {len(resolvable)}")
    print(f"Not resolvable: {len(rows) - len(resolvable)}")

    by_reason: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        by_reason[str(row["skip_reason"])][str(row["resolvable"])] += 1
    print("\nBy skip_reason:")
    for reason, counts in sorted(by_reason.items(), key=lambda x: -sum(x[1].values())):
        print(
            f"  {reason}: resolvable={counts.get('yes', 0)} "
            f"not_resolvable={counts.get('no', 0)}"
        )

    if resolvable:
        print("\nResolvable rows:")
        for row in resolvable[:25]:
            print(
                f"  id={row['row_id']} chat={row['telegram_chat_id']} "
                f"skip={row['skip_reason']!r} player={row['discovered_player_id']} "
                f"@{row['discovered_username'] or '?'} title={row['group_title'][:40]!r}"
            )
        if len(resolvable) > 25:
            print(f"  ... and {len(resolvable) - 25} more (see CSV)")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--club", required=True, choices=CLUB_KEYS)
    parser.add_argument("--row-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--all-tiers",
        action="store_true",
        help="Include all tiers (default: club recovery tier scope only).",
    )
    parser.add_argument("--delay-sec", type=float, default=0.05)
    parser.add_argument("-o", "--output", type=Path, default=None)
    args = parser.parse_args()

    rows = await run_audit(
        club_filter=args.club,
        row_id=args.row_id,
        limit=args.limit,
        all_tiers=bool(args.all_tiers),
        delay_sec=float(args.delay_sec),
    )
    if not rows:
        print("No matching skipped rows.")
        return 0

    _print_summary(rows)
    output_path = args.output or _default_output_path(args.club)
    _write_csv(output_path, rows)
    print(f"\nWrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
