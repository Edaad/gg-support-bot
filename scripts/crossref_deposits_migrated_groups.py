"""Cross-reference deposit groups with supergroup migration affected chats.

Joins ``gc_deposits_by_group.csv`` with affected groups from the pre-migration
``pg_dump`` (same source as ``readd_migrated_group_members.py``). Writes a CSV
of deposit groups whose chat was a basic group before migration and is now a
supergroup (``status=migrated``).

Usage:
  python scripts/crossref_deposits_migrated_groups.py
  python scripts/crossref_deposits_migrated_groups.py --output gc_deposits_migrated_groups.csv
  python scripts/crossref_deposits_migrated_groups.py --include-not-migrated-yet
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
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


@dataclass(frozen=True)
class MigratedIndexEntry:
    club_id: int
    club_key: str
    group_title: str
    old_chat_id: int
    current_chat_id: int
    migration_status: str


def _default_deposits_csv() -> Path:
    return _REPO_ROOT / "gc_deposits_by_group.csv"


def _default_output_csv() -> Path:
    return _REPO_ROOT / "gc_deposits_migrated_groups.csv"


def _norm_title(title: str) -> str:
    return " ".join((title or "").strip().split())


def _build_migrated_index(
    affected: list[AffectedMigratedGroup],
    *,
    include_not_migrated_yet: bool,
) -> tuple[
    dict[int, MigratedIndexEntry],
    dict[tuple[int, str], MigratedIndexEntry],
]:
    from club_gc_settings import get_club_gc_config_by_link_club_id

    allowed_statuses = {"migrated"}
    if include_not_migrated_yet:
        allowed_statuses.add("not_migrated_yet")

    by_chat: dict[int, MigratedIndexEntry] = {}
    by_club_title: dict[tuple[int, str], MigratedIndexEntry] = {}

    for row in affected:
        if row.status not in allowed_statuses:
            continue
        if row.current_chat_id is None:
            continue
        cfg = get_club_gc_config_by_link_club_id(int(row.club_id))
        entry = MigratedIndexEntry(
            club_id=int(row.club_id),
            club_key=cfg.club_key if cfg else "",
            group_title=row.title,
            old_chat_id=int(row.old_chat_id),
            current_chat_id=int(row.current_chat_id),
            migration_status=row.status,
        )
        for cid in (entry.old_chat_id, entry.current_chat_id):
            for variant in telegram_chat_id_variants(cid):
                by_chat.setdefault(int(variant), entry)
        title_key = (entry.club_id, _norm_title(entry.group_title))
        by_club_title.setdefault(title_key, entry)

    return by_chat, by_club_title


def _match_deposit_row(
    row: dict[str, str],
    *,
    by_chat: dict[int, MigratedIndexEntry],
    by_club_title: dict[tuple[int, str], MigratedIndexEntry],
) -> tuple[MigratedIndexEntry | None, str]:
    try:
        chat_id = int(row["telegram_chat_id"])
    except (KeyError, TypeError, ValueError):
        return None, ""

    for variant in telegram_chat_id_variants(chat_id):
        hit = by_chat.get(int(variant))
        if hit is not None:
            if int(variant) == hit.current_chat_id:
                return hit, "current_chat_id"
            if int(variant) == hit.old_chat_id:
                return hit, "old_chat_id"
            return hit, "chat_id_variant"

    club_id_raw = (row.get("club_id") or "").strip()
    title = _norm_title(row.get("gc_title") or row.get("group_name") or "")
    if club_id_raw.isdigit() and title:
        hit = by_club_title.get((int(club_id_raw), title))
        if hit is not None:
            return hit, "club_title"

    return None, ""


def crossref(
    deposits_path: Path,
    *,
    backup_path: Path | None,
    output_path: Path,
    include_not_migrated_yet: bool,
) -> dict[str, int]:
    from club_gc_settings import CLUB_GC_CONFIG
    from db.connection import init_engine

    if not deposits_path.is_file():
        raise SystemExit(f"Deposits CSV not found: {deposits_path}")

    init_engine()
    dump_path = (backup_path or find_earliest_upgrade_backup(_REPO_ROOT)).resolve()
    mtproto_club_ids = frozenset(int(cfg.link_club_id) for cfg in CLUB_GC_CONFIG.values())
    affected = resolve_affected_from_backup(
        dump_path,
        mtproto_club_ids=mtproto_club_ids,
    )
    by_chat, by_club_title = _build_migrated_index(
        affected,
        include_not_migrated_yet=include_not_migrated_yet,
    )

    deposit_rows: list[dict[str, str]] = []
    with deposits_path.open(newline="", encoding="utf-8") as f:
        deposit_rows = list(csv.DictReader(f))

    out_rows: list[dict[str, str]] = []
    matched = 0
    for row in deposit_rows:
        hit, matched_by = _match_deposit_row(
            row,
            by_chat=by_chat,
            by_club_title=by_club_title,
        )
        if hit is None:
            continue
        matched += 1
        out = dict(row)
        out["migration_status"] = hit.migration_status
        out["matched_by"] = matched_by
        out["old_chat_id"] = str(hit.old_chat_id)
        out["current_chat_id"] = str(hit.current_chat_id)
        out["migration_group_title"] = hit.group_title
        if not (out.get("club_key") or "").strip():
            out["club_key"] = hit.club_key
        out_rows.append(out)

    base_fields = list(deposit_rows[0].keys()) if deposit_rows else []
    extra_fields = [
        "migration_status",
        "matched_by",
        "old_chat_id",
        "current_chat_id",
        "migration_group_title",
    ]
    fieldnames = base_fields + [f for f in extra_fields if f not in base_fields]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(out_rows)

    migrated_affected = sum(1 for a in affected if a.status == "migrated")
    return {
        "backup_path": str(dump_path),
        "deposits_total": len(deposit_rows),
        "affected_migrated": migrated_affected,
        "matched": matched,
        "output": str(output_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-csv",
        type=Path,
        default=_default_deposits_csv(),
        help="Deposit groups CSV (default: gc_deposits_by_group.csv).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_default_output_csv(),
        help="Output CSV (default: gc_deposits_migrated_groups.csv).",
    )
    parser.add_argument(
        "--backup",
        type=Path,
        help="Pre-migration pg_dump (default: earliest backups/upgrade_supergroup_*/database.dump).",
    )
    parser.add_argument(
        "--include-not-migrated-yet",
        action="store_true",
        help="Also include deposit groups still on legacy basic chat ids.",
    )
    args = parser.parse_args()

    stats = crossref(
        args.input_csv,
        backup_path=args.backup,
        output_path=args.output,
        include_not_migrated_yet=bool(args.include_not_migrated_yet),
    )
    print(f"Backup: {stats['backup_path']}")
    print(
        f"Deposits: {stats['deposits_total']} | "
        f"affected migrated (all clubs): {stats['affected_migrated']} | "
        f"matched deposit groups: {stats['matched']}"
    )
    print(f"Wrote: {stats['output']}")


if __name__ == "__main__":
    main()
