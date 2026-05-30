"""Export payment methods, tiers, variants, sub-options, and club messages to CSV.

Run before migrate_legacy_method_to_tiers.py (or any bulk config change):

  python export_payment_config_backup.py
  python export_payment_config_backup.py --output-dir backups/my_snapshot

Writes one CSV per table under a timestamped folder plus manifest.txt.
Requires DATABASE_URL (loads .env automatically).
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from db.connection import get_db
from db.models import (
    Club,
    CustomCommand,
    Group,
    MethodVariant,
    PaymentMethod,
    PaymentMethodTier,
    PaymentSubOption,
)

# (filename, model, order_by column names or None)
EXPORT_TABLES: Sequence[tuple[str, type, Optional[tuple[str, ...]]]] = (
    ("clubs.csv", Club, ("id",)),
    ("payment_methods.csv", PaymentMethod, ("club_id", "direction", "sort_order", "id")),
    ("payment_method_tiers.csv", PaymentMethodTier, ("method_id", "sort_order", "id")),
    ("method_variants.csv", MethodVariant, ("method_id", "tier_id", "sort_order", "id")),
    ("payment_sub_options.csv", PaymentSubOption, ("method_id", "sort_order", "id")),
    ("custom_commands.csv", CustomCommand, ("club_id", "command_name")),
    ("groups.csv", Group, ("club_id", "chat_id")),
)

# Club columns that are bot-facing message / copy (not operational settings).
CLUB_MESSAGE_COLUMNS = (
    "id",
    "name",
    "welcome_type",
    "welcome_text",
    "welcome_file_id",
    "welcome_caption",
    "member_join_preamble_text",
    "member_join_tos_file_id",
    "member_join_tos_caption",
    "list_type",
    "list_text",
    "list_file_id",
    "list_caption",
    "deposit_simple_type",
    "deposit_simple_text",
    "deposit_simple_file_id",
    "deposit_simple_caption",
    "cashout_simple_type",
    "cashout_simple_text",
    "cashout_simple_file_id",
    "cashout_simple_caption",
)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat()
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _model_columns(model: type) -> list[str]:
    return [c.key for c in inspect(model).mapper.column_attrs]


def _query_all(session: Session, model: type, order_by: Optional[tuple[str, ...]]):
    q = session.query(model)
    if order_by:
        for col in order_by:
            q = q.order_by(getattr(model, col))
    return q.all()


def _write_csv(path: Path, columns: list[str], rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _cell(row.get(k)) for k in columns})
            count += 1
    return count


def _export_club_messages(session: Session, path: Path) -> int:
    rows = []
    for club in session.query(Club).order_by(Club.id):
        rows.append({col: getattr(club, col) for col in CLUB_MESSAGE_COLUMNS})
    return _write_csv(path, list(CLUB_MESSAGE_COLUMNS), rows)


def _default_output_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Path("backups") / f"payment_config_{stamp}"


def export_all(session: Session, output_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for filename, model, order_by in EXPORT_TABLES:
        columns = _model_columns(model)
        objects = _query_all(session, model, order_by)
        rows = [{col: getattr(obj, col) for col in columns} for obj in objects]
        counts[filename] = _write_csv(output_dir / filename, columns, rows)
    counts["club_messages.csv"] = _export_club_messages(session, output_dir / "club_messages.csv")
    return counts


def _write_manifest(output_dir: Path, counts: dict[str, int]) -> None:
    lines = [
        "Payment configuration backup",
        f"Created (UTC): {datetime.now(timezone.utc).isoformat()}",
        "",
        "Files:",
    ]
    for name, n in sorted(counts.items()):
        lines.append(f"  {name}: {n} row(s)")
    lines.extend([
        "",
        "Restore: use these CSVs as reference; re-import is manual or via a restore script.",
        "Run before: python migrate_legacy_method_to_tiers.py",
    ])
    (output_dir / "manifest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Optional[Iterable[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for CSV files (default: backups/payment_config_YYYYMMDD_HHMMSS)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    output_dir = args.output_dir or _default_output_dir()
    output_dir = output_dir.resolve()

    with get_db() as session:
        counts = export_all(session, output_dir)

    _write_manifest(output_dir, counts)

    total = sum(counts.values())
    print(f"Backup written to {output_dir}")
    for name, n in sorted(counts.items()):
        print(f"  {name}: {n} rows")
    print(f"Total rows: {total}")
    print(f"Manifest: {output_dir / 'manifest.txt'}")


if __name__ == "__main__":
    main()
