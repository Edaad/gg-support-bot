"""Seed migrated_group_recovery from backup-affected supergroups + live DB signals.

Priority: deposit players (tier 1) > active in past N days (tier 2) > rest (tier 3).

Usage:
  python scripts/seed_migrated_group_recovery.py
  python scripts/seed_migrated_group_recovery.py --status
  python scripts/seed_migrated_group_recovery.py --days 30 --deposits-csv gc_deposits_by_group.csv
  python scripts/seed_migrated_group_recovery.py --affected-csv backups/affected_migrated_groups_*.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from bot.services.migration_recovery_priority import (  # noqa: E402
    classify_priority_tier,
    compute_priority_rank,
)
from bot.services.migration_group_readd import (  # noqa: E402
    load_player_display_names_by_chat,
    load_player_rows_by_chat,
)
from scripts.backup_groups_reader import (  # noqa: E402
    AffectedMigratedGroup,
    find_earliest_upgrade_backup,
    resolve_affected_from_backup,
)
from scripts.migrated_groups_activity_report import (  # noqa: E402
    GroupAgg,
    MigratedGroupRow,
    _collect_activity,
    _load_affected_from_csv,
    _migrated_groups,
)

logger = logging.getLogger("seed_migrated_group_recovery")


@dataclass(frozen=True)
class SeedCandidate:
    telegram_chat_id: int
    club_key: str
    club_id: int
    group_title: str
    old_chat_id: int
    player_telegram_user_id: int | None
    player_username: str | None
    player_display_name: str | None
    priority_tier: int
    priority_rank: int
    deposit_cents: int
    last_activity_epoch: int


def _load_deposits_csv(path: Path) -> dict[int, int]:
    """Map telegram_chat_id -> total deposited cents (all-time)."""
    if not path.is_file():
        return {}
    out: dict[int, int] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw_chat = (row.get("telegram_chat_id") or "").strip()
            raw_usd = (row.get("total_deposited_usd") or "").strip()
            if not raw_chat:
                continue
            try:
                chat_id = int(raw_chat)
                usd = Decimal(raw_usd or "0")
                cents = int(usd * 100)
            except (TypeError, ValueError):
                continue
            if cents > 0:
                out[chat_id] = max(out.get(chat_id, 0), cents)
    return out


def _is_active_group(agg: GroupAgg | None) -> bool:
    if agg is None:
        return False
    return bool(agg.signals) or agg.has_payment_activity or agg.has_identifiable_user_activity


def _activity_epoch(agg: GroupAgg | None) -> int:
    if agg is None or agg.last_activity_at is None:
        return 0
    dt = agg.last_activity_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def build_seed_candidates(
    groups: list[MigratedGroupRow],
    *,
    deposit_by_chat: dict[int, int],
    activity_by_chat: dict[int, GroupAgg],
) -> list[SeedCandidate]:
    chat_ids = {int(g.current_chat_id) for g in groups}
    player_map = load_player_rows_by_chat(chat_ids)
    display_map = load_player_display_names_by_chat(chat_ids)

    tier_buckets: dict[int, list[tuple[MigratedGroupRow, int, int]]] = {1: [], 2: [], 3: []}

    for group in groups:
        chat_id = int(group.current_chat_id)
        deposit_cents = int(deposit_by_chat.get(chat_id, 0))
        agg = activity_by_chat.get(chat_id)
        active = _is_active_group(agg)
        tier = classify_priority_tier(
            deposit_cents=deposit_cents,
            active_in_past_30_days=active,
        )
        tier_buckets[tier].append(
            (group, deposit_cents, _activity_epoch(agg))
        )

    def sort_key_tier1(item: tuple[MigratedGroupRow, int, int]) -> tuple:
        _g, dep, _act = item
        return (-dep, int(item[0].current_chat_id))

    def sort_key_tier2(item: tuple[MigratedGroupRow, int, int]) -> tuple:
        _g, _dep, act = item
        return (-act, int(item[0].current_chat_id))

    def sort_key_tier3(item: tuple[MigratedGroupRow, int, int]) -> tuple:
        return (int(item[0].current_chat_id),)

    sorted_items: list[tuple[MigratedGroupRow, int, int, int, int]] = []
    for tier, sorter in ((1, sort_key_tier1), (2, sort_key_tier2), (3, sort_key_tier3)):
        bucket = sorted(tier_buckets[tier], key=sorter)
        for seq, (group, deposit_cents, activity_epoch) in enumerate(bucket):
            sorted_items.append((group, deposit_cents, activity_epoch, tier, seq))

    candidates: list[SeedCandidate] = []
    for group, deposit_cents, activity_epoch, tier, seq in sorted_items:
        chat_id = int(group.current_chat_id)
        player_id, player_username, _club_key = player_map.get(chat_id, (None, None, None))
        rank = compute_priority_rank(
            priority_tier=tier,
            deposit_cents=deposit_cents,
            last_activity_epoch=activity_epoch,
            telegram_chat_id=chat_id,
            sequence=seq,
        )
        candidates.append(
            SeedCandidate(
                telegram_chat_id=chat_id,
                club_key=group.club_key,
                club_id=int(group.club_id),
                group_title=group.group_title,
                old_chat_id=int(group.old_chat_id),
                player_telegram_user_id=player_id,
                player_username=player_username,
                player_display_name=display_map.get(chat_id),
                priority_tier=tier,
                priority_rank=rank,
                deposit_cents=deposit_cents,
                last_activity_epoch=activity_epoch,
            )
        )
    return candidates


def upsert_candidates(candidates: list[SeedCandidate]) -> tuple[int, int]:
    from sqlalchemy.dialects.postgresql import insert

    from db.connection import get_db, init_engine
    from db.models import MigratedGroupRecovery

    init_engine()
    inserted = 0
    skipped = 0
    now = datetime.now(timezone.utc)

    with get_db() as session:
        for cand in candidates:
            stmt = (
                insert(MigratedGroupRecovery)
                .values(
                    telegram_chat_id=cand.telegram_chat_id,
                    club_key=cand.club_key,
                    club_id=cand.club_id,
                    group_title=cand.group_title,
                    old_chat_id=cand.old_chat_id,
                    player_telegram_user_id=cand.player_telegram_user_id,
                    player_username=cand.player_username,
                    player_display_name=cand.player_display_name,
                    priority_tier=cand.priority_tier,
                    priority_rank=cand.priority_rank,
                    readd_status="pending",
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=["telegram_chat_id"])
            )
            result = session.execute(stmt)
            if result.rowcount:
                inserted += 1
            else:
                skipped += 1
        session.commit()
    return inserted, skipped


def _load_affected(
    *,
    backup_path: Path,
    affected_csv: Path | None,
    club_key_filter: str | None,
) -> list[AffectedMigratedGroup]:
    from club_gc_settings import CLUB_GC_CONFIG

    mtproto_club_ids = frozenset(int(cfg.link_club_id) for cfg in CLUB_GC_CONFIG.values())
    club_id_filter: int | None = None
    if club_key_filter:
        cfg = CLUB_GC_CONFIG.get(club_key_filter)
        if cfg is None:
            raise SystemExit(f"Unknown club_key: {club_key_filter!r}")
        club_id_filter = int(cfg.link_club_id)

    if affected_csv is not None:
        affected = _load_affected_from_csv(affected_csv)
    else:
        affected = resolve_affected_from_backup(
            backup_path,
            mtproto_club_ids=mtproto_club_ids,
            club_id_filter=club_id_filter,
            chat_id_filter=None,
        )
    return affected


def run_seed(
    *,
    backup_path: Path,
    affected_csv: Path | None,
    deposits_csv: Path,
    days: int,
    club_key_filter: str | None,
) -> dict[str, int | str]:
    from db.connection import init_engine

    init_engine()
    affected = _load_affected(
        backup_path=backup_path,
        affected_csv=affected_csv,
        club_key_filter=club_key_filter,
    )
    groups = _migrated_groups(affected, club_key_filter=club_key_filter)
    if not groups:
        return {"groups": 0, "inserted": 0, "skipped": 0, "message": "no migrated groups"}

    activity_by_chat, _user_aggs = _collect_activity(groups, days=int(days))
    deposit_by_chat = _load_deposits_csv(deposits_csv)
    candidates = build_seed_candidates(
        groups,
        deposit_by_chat=deposit_by_chat,
        activity_by_chat=activity_by_chat,
    )
    inserted, skipped = upsert_candidates(candidates)
    tier_counts = {1: 0, 2: 0, 3: 0}
    for c in candidates:
        tier_counts[c.priority_tier] = tier_counts.get(c.priority_tier, 0) + 1
    return {
        "groups": len(groups),
        "inserted": inserted,
        "skipped": skipped,
        "tier_1": tier_counts[1],
        "tier_2": tier_counts[2],
        "tier_3": tier_counts[3],
        "backup": str(backup_path),
    }


def print_status() -> None:
    from bot.services.migration_recovery import recovery_status_counts

    counts = recovery_status_counts()
    print("migrated_group_recovery status")
    print("By readd_status:")
    for key in sorted(counts["by_status"]):
        print(f"  {key}: {counts['by_status'][key]}")
    print("By priority_tier:")
    for key in sorted(counts["by_tier"]):
        print(f"  tier {key}: {counts['by_tier'][key]}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, help="Pre-migration pg_dump path.")
    parser.add_argument("--affected-csv", type=Path, help="Pre-generated affected-groups CSV.")
    parser.add_argument(
        "--deposits-csv",
        type=Path,
        default=_REPO_ROOT / "gc_deposits_by_group.csv",
        help="Deposit totals CSV (default: gc_deposits_by_group.csv).",
    )
    parser.add_argument("--days", type=int, default=30, help="Activity window for tier 2.")
    parser.add_argument("--club-key", choices=["round_table", "creator_club", "clubgto"])
    parser.add_argument("--status", action="store_true", help="Print table counts and exit.")
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    backup_path = args.backup.resolve() if args.backup else find_earliest_upgrade_backup(_REPO_ROOT).resolve()
    stats = run_seed(
        backup_path=backup_path,
        affected_csv=args.affected_csv,
        deposits_csv=args.deposits_csv,
        days=int(args.days),
        club_key_filter=args.club_key,
    )
    print(f"Backup/affected source: {stats.get('backup', '')}")
    print(
        f"Groups={stats['groups']} inserted={stats['inserted']} skipped(existing)={stats['skipped']} "
        f"tier1={stats.get('tier_1', 0)} tier2={stats.get('tier_2', 0)} tier3={stats.get('tier_3', 0)}"
    )


if __name__ == "__main__":
    main()
