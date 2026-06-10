"""List migrated affected groups not in deposits or active invite-target CSVs.

For each club (round_table, creator_club, clubgto), subtracts groups present in
``gc_deposits_migrated_groups.csv`` or the club's active invite-target CSV from
the full set of migrated affected groups (backup + live DB). Writes one gap CSV
per club.

Usage:
  python scripts/crossref_affected_gap.py
  python scripts/crossref_affected_gap.py --club-key clubgto
  python scripts/crossref_affected_gap.py --output-dir .
  python scripts/crossref_affected_gap.py --affected-csv backups/affected_migrated_groups_*.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from notification.chat_id import telegram_chat_id_variants  # noqa: E402
from scripts.backup_groups_reader import (  # noqa: E402
    AffectedMigratedGroup,
    find_earliest_upgrade_backup,
    resolve_affected_from_backup,
)
from scripts.migrated_groups_activity_report import _load_affected_from_csv  # noqa: E402

OUTPUT_FIELDS = [
    "club_id",
    "club_key",
    "club_name",
    "group_title",
    "old_chat_id",
    "current_chat_id",
    "status",
]

DEFAULT_CLUBS = ("round_table", "creator_club", "clubgto")

DEFAULT_ACTIVE_CSV_BY_CLUB: dict[str, Path] = {
    "round_table": _REPO_ROOT / "gc_active_migrated_invite_targets.csv",
    "creator_club": _REPO_ROOT / "gc_active_migrated_invite_targets.csv",
    "clubgto": _REPO_ROOT / "gc_active_migrated_invite_targets_clubgto.csv",
}

DEFAULT_OUTPUT_BY_CLUB: dict[str, str] = {
    "round_table": "gc_affected_not_covered_round_table.csv",
    "creator_club": "gc_affected_not_covered_creator_club.csv",
    "clubgto": "gc_affected_not_covered_clubgto.csv",
}

CHAT_ID_COLUMNS = ("current_chat_id", "telegram_chat_id", "old_chat_id")


def _default_deposits_migrated_csv() -> Path:
    return _REPO_ROOT / "gc_deposits_migrated_groups.csv"


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise SystemExit(f"CSV not found: {path}")
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _chat_id_variants_from_rows(rows: list[dict[str, str]]) -> set[int]:
    raw: set[int] = set()
    for row in rows:
        for key in CHAT_ID_COLUMNS:
            raw_val = (row.get(key) or "").strip()
            if not raw_val:
                continue
            try:
                raw.add(int(raw_val))
            except ValueError:
                continue
    out: set[int] = set()
    for cid in raw:
        for variant in telegram_chat_id_variants(cid):
            out.add(int(variant))
    return out


def _is_excluded(current_chat_id: int, excluded: set[int]) -> bool:
    for variant in telegram_chat_id_variants(current_chat_id):
        if int(variant) in excluded:
            return True
    return False


def _load_affected(
    *,
    affected_csv: Path | None,
    backup_path: Path | None,
) -> list[AffectedMigratedGroup]:
    if affected_csv is not None:
        return _load_affected_from_csv(affected_csv)

    from club_gc_settings import CLUB_GC_CONFIG
    from db.connection import init_engine

    init_engine()
    dump_path = (backup_path or find_earliest_upgrade_backup(_REPO_ROOT)).resolve()
    mtproto_club_ids = frozenset(int(cfg.link_club_id) for cfg in CLUB_GC_CONFIG.values())
    return resolve_affected_from_backup(
        dump_path,
        mtproto_club_ids=mtproto_club_ids,
    )


def _club_name_by_key() -> dict[str, str]:
    from club_gc_settings import CLUB_GC_CONFIG

    return {key: cfg.club_display_name for key, cfg in CLUB_GC_CONFIG.items()}


def _gap_rows_for_club(
    affected: list[AffectedMigratedGroup],
    *,
    club_key: str,
    club_id: int,
    club_name: str,
    excluded: set[int],
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in affected:
        if row.status != "migrated" or row.current_chat_id is None:
            continue
        if int(row.club_id) != int(club_id):
            continue
        current_chat_id = int(row.current_chat_id)
        if _is_excluded(current_chat_id, excluded):
            continue
        out.append(
            {
                "club_id": str(club_id),
                "club_key": club_key,
                "club_name": club_name,
                "group_title": row.title,
                "old_chat_id": str(row.old_chat_id),
                "current_chat_id": str(current_chat_id),
                "status": row.status,
            }
        )
    out.sort(key=lambda r: (r["group_title"].lower(), r["current_chat_id"]))
    return out


def _write_gap_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def crossref_gap(
    *,
    deposits_migrated_path: Path,
    active_csv_by_club: dict[str, Path],
    output_dir: Path,
    club_keys: tuple[str, ...],
    affected_csv: Path | None,
    backup_path: Path | None,
) -> dict[str, dict[str, int | str]]:
    from club_gc_settings import CLUB_GC_CONFIG

    affected = _load_affected(affected_csv=affected_csv, backup_path=backup_path)
    deposit_rows = _load_csv_rows(deposits_migrated_path)
    club_names = _club_name_by_key()

    stats: dict[str, dict[str, int | str]] = {}
    for club_key in club_keys:
        cfg = CLUB_GC_CONFIG.get(club_key)
        if cfg is None:
            raise SystemExit(f"Unknown club_key: {club_key!r}")

        club_id = int(cfg.link_club_id)
        active_path = active_csv_by_club[club_key]
        active_rows = _load_csv_rows(active_path)

        club_deposit_rows = [r for r in deposit_rows if (r.get("club_key") or "").strip() == club_key]
        club_active_rows = [r for r in active_rows if (r.get("club_key") or "").strip() == club_key]
        excluded = _chat_id_variants_from_rows(club_deposit_rows) | _chat_id_variants_from_rows(
            club_active_rows
        )

        migrated_count = sum(
            1
            for row in affected
            if row.status == "migrated"
            and row.current_chat_id is not None
            and int(row.club_id) == club_id
        )
        gap_rows = _gap_rows_for_club(
            affected,
            club_key=club_key,
            club_id=club_id,
            club_name=club_names.get(club_key, cfg.club_display_name),
            excluded=excluded,
        )
        out_path = output_dir / DEFAULT_OUTPUT_BY_CLUB[club_key]
        _write_gap_csv(out_path, gap_rows)

        stats[club_key] = {
            "migrated_affected": migrated_count,
            "excluded": len(excluded),
            "gap": len(gap_rows),
            "output": str(out_path),
            "active_csv": str(active_path),
        }

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--deposits-migrated-csv",
        type=Path,
        default=_default_deposits_migrated_csv(),
        help="Migrated deposit groups CSV (default: gc_deposits_migrated_groups.csv).",
    )
    parser.add_argument(
        "--active-csv-round-table",
        type=Path,
        default=DEFAULT_ACTIVE_CSV_BY_CLUB["round_table"],
        help="Active invite targets for round_table.",
    )
    parser.add_argument(
        "--active-csv-creator-club",
        type=Path,
        default=DEFAULT_ACTIVE_CSV_BY_CLUB["creator_club"],
        help="Active invite targets for creator_club.",
    )
    parser.add_argument(
        "--active-csv-clubgto",
        type=Path,
        default=DEFAULT_ACTIVE_CSV_BY_CLUB["clubgto"],
        help="Active invite targets for clubgto.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_REPO_ROOT,
        help="Directory for per-club gap CSVs (default: repo root).",
    )
    parser.add_argument(
        "--club-key",
        choices=DEFAULT_CLUBS,
        action="append",
        dest="club_keys",
        help="Limit to one or more clubs (default: all three).",
    )
    parser.add_argument(
        "--affected-csv",
        type=Path,
        help="Precomputed affected-groups CSV (skips live DB lookup).",
    )
    parser.add_argument(
        "--backup",
        type=Path,
        help="Pre-migration pg_dump (default: earliest backups/upgrade_supergroup_*/database.dump).",
    )
    args = parser.parse_args()

    club_keys = tuple(args.club_keys) if args.club_keys else DEFAULT_CLUBS
    active_csv_by_club = {
        "round_table": args.active_csv_round_table,
        "creator_club": args.active_csv_creator_club,
        "clubgto": args.active_csv_clubgto,
    }

    stats = crossref_gap(
        deposits_migrated_path=args.deposits_migrated_csv,
        active_csv_by_club=active_csv_by_club,
        output_dir=args.output_dir,
        club_keys=club_keys,
        affected_csv=args.affected_csv,
        backup_path=args.backup,
    )

    total_gap = 0
    for club_key in club_keys:
        row = stats[club_key]
        total_gap += int(row["gap"])
        print(
            f"{club_key}: migrated={row['migrated_affected']} | "
            f"excluded_chat_variants={row['excluded']} | gap={row['gap']}"
        )
        print(f"  active_csv: {row['active_csv']}")
        print(f"  wrote: {row['output']}")
    print(f"Total gap rows: {total_gap}")


if __name__ == "__main__":
    main()
