"""Bulk stage inactive outreach rows from a least-active / enriched CSV.

Uses ``stage_inactive_group`` (same as ``/stageinactive``) — Postgres only, no Telethon.

Usage:
  python scripts/bulk_stage_inactive_from_csv.py \\
    --csv backups/least_active_megagroups_v2_two_members.csv

  python scripts/bulk_stage_inactive_from_csv.py \\
    --csv backups/least_active_megagroups_v2_two_members.csv --apply

  python scripts/bulk_stage_inactive_from_csv.py \\
    --csv backups/least_active_megagroups_v2_two_members.csv --chat-id -100123 --apply
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
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

from bot.services.inactive_group_outreach_staging import (
    is_megagroup_chat_id,
    resolve_club_key_for_chat,
    stage_inactive_group,
)
from config import ADMIN_USER_IDS


@dataclass
class CsvTarget:
    chat_id: int
    title: str
    club_key: str | None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk stage inactive groups from CSV.")
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument(
        "--club-key",
        default=None,
        help="Force club_key when title/DB resolution fails (e.g. round_table).",
    )
    parser.add_argument("--chat-id", type=int, default=None, help="Stage one chat id only.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip", type=int, default=0)
    parser.add_argument(
        "--note",
        default="bulk_stage_inactive_from_csv",
        help="stage_note stored on each row.",
    )
    parser.add_argument(
        "--staged-by-user-id",
        type=int,
        default=int(ADMIN_USER_IDS[0]) if ADMIN_USER_IDS else 0,
        help="Telegram user id for staged_by_telegram_user_id (default: first ADMIN_USER_IDS).",
    )
    parser.add_argument("--apply", action="store_true", help="Persist staging (default: dry-run).")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write result CSV (default: backups/bulk_stage_<input_stem>_<ts>.csv).",
    )
    return parser.parse_args()


def _load_targets(args: argparse.Namespace) -> list[CsvTarget]:
    path = args.csv
    if not path.is_file():
        raise SystemExit(f"CSV not found: {path}")

    out: list[CsvTarget] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw_id = (row.get("chat_id") or row.get("telegram_chat_id") or "").strip()
            if not raw_id:
                continue
            chat_id = int(raw_id)
            if args.chat_id is not None and chat_id != int(args.chat_id):
                continue
            title = (row.get("title") or row.get("group_title") or "").strip()
            club_key = args.club_key or resolve_club_key_for_chat(chat_id, title)
            out.append(CsvTarget(chat_id=chat_id, title=title, club_key=club_key))

    if args.skip:
        out = out[int(args.skip) :]
    if args.limit is not None:
        out = out[: max(0, int(args.limit))]
    return out


def main() -> int:
    args = _parse_args()
    targets = _load_targets(args)
    if not targets:
        print("No targets matched.", file=sys.stderr)
        return 1

    dry_run = not args.apply
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = args.output or (
        _REPO_ROOT / "backups" / f"bulk_stage_{args.csv.stem}_{ts}.csv"
    )

    staged = 0
    already = 0
    skipped = 0
    failed = 0
    result_rows: list[dict[str, str]] = []

    for target in targets:
        if not is_megagroup_chat_id(target.chat_id):
            skipped += 1
            result_rows.append(
                {
                    "chat_id": str(target.chat_id),
                    "title": target.title,
                    "status": "skipped",
                    "error": "not_megagroup",
                }
            )
            continue
        if not target.club_key:
            skipped += 1
            result_rows.append(
                {
                    "chat_id": str(target.chat_id),
                    "title": target.title,
                    "status": "skipped",
                    "error": "club_key_unresolved",
                }
            )
            continue

        if dry_run:
            result_rows.append(
                {
                    "chat_id": str(target.chat_id),
                    "title": target.title,
                    "club_key": target.club_key,
                    "status": "dry_run",
                    "error": "",
                }
            )
            staged += 1
            continue

        result = stage_inactive_group(
            club_key=target.club_key,
            telegram_chat_id=target.chat_id,
            group_title=target.title or f"(csv chat {target.chat_id})",
            staged_by_user_id=int(args.staged_by_user_id),
            note=args.note,
        )
        if not result.ok:
            failed += 1
            result_rows.append(
                {
                    "chat_id": str(target.chat_id),
                    "title": target.title,
                    "club_key": target.club_key or "",
                    "status": "failed",
                    "error": result.error or "unknown",
                }
            )
            continue

        if result.already_staged:
            already += 1
            status = "already_staged"
        else:
            staged += 1
            status = "staged"

        result_rows.append(
            {
                "chat_id": str(target.chat_id),
                "title": target.title,
                "club_key": target.club_key,
                "row_id": str(result.row_id or ""),
                "status": status,
                "error": "",
            }
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["chat_id", "title", "club_key", "row_id", "status", "error"]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result_rows)

    mode = "dry-run" if dry_run else "apply"
    print(
        f"{mode}: targets={len(targets)} staged={staged} already_staged={already} "
        f"skipped={skipped} failed={failed}"
    )
    print(f"Wrote {out_path}")
    if dry_run:
        print("Pass --apply to persist.", file=sys.stderr)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
