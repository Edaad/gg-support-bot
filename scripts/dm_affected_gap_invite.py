"""DM players invite links for supergroup-migrated groups not yet covered.

Reads ``gc_affected_not_covered_*.csv`` (from ``crossref_affected_gap.py``) — groups
that were migrated but are not in the deposited or active invite-target lists.
Resolves each player from ``support_group_chats.player_telegram_user_id`` and sends
``PLAYER_MIGRATION_UPGRADE_INVITE_MESSAGE`` via the club MTProto session.

Progress is tracked in ``backups/dm_affected_gap_invite_tracker.csv`` (override with
``--tracker-csv``). Re-runs skip chats/players already recorded as ``dm_sent``.

Players who could not be DM'd are merged into ``backups/dm_affected_gap_invite_failed.csv``
(keyed by ``telegram_chat_id``, last failure wins) for manual follow-up.

Dry-run by default; pass ``--apply`` to send DMs. Optionally refresh invite links
with ``--export-invite-links`` (and ``--update-invite-links`` to write Postgres).

Environment: DATABASE_URL, TG_API_ID, TG_API_HASH (same as other MTProto scripts).

Operational: do not run while the Heroku worker holds the same club Telethon
session. Set ``GC_MTPROTO_ENABLED=false`` (or ``GC_DM_GC_LISTENER_ENABLED=false``)
on the worker and restart before running; re-enable after.

Usage:
  python scripts/dm_affected_gap_invite.py --club-key round_table --limit 5
  python scripts/dm_affected_gap_invite.py --apply --club-key clubgto --dm-delay 3 --export-invite-links
  python scripts/dm_affected_gap_invite.py --apply --club-key round_table --dm-delay 3
  python scripts/dm_affected_gap_invite.py --apply --dm-delay 3 --export-invite-links
  python scripts/dm_affected_gap_invite.py --input-csv gc_affected_not_covered_round_table.csv --apply
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from dataclasses import asdict
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

from scripts.backfill_support_group_invite_links import (  # noqa: E402
    CLUB_KEYS,
    _configure_logging,
)
from scripts.dm_deposit_groups_invite import (  # noqa: E402
    DmResult,
    DmTracker,
    FAILED_CSV_FIELDS,
    _failed_dm_results,
    _load_targets_from_csv,
    _print_human,
    _run,
    _write_results_csv,
)

GAP_CSV_BY_CLUB: dict[str, str] = {
    "round_table": "gc_affected_not_covered_round_table.csv",
    "creator_club": "gc_affected_not_covered_creator_club.csv",
    "clubgto": "gc_affected_not_covered_clubgto.csv",
}

GAP_CLUB_ORDER = ("round_table", "creator_club", "clubgto")

FAILED_CSV_FIELDS_WITH_AT = [*FAILED_CSV_FIELDS, "failed_at"]


def _default_tracker_csv() -> Path:
    return _REPO_ROOT / "backups" / "dm_affected_gap_invite_tracker.csv"


def _default_failed_csv() -> Path:
    return _REPO_ROOT / "backups" / "dm_affected_gap_invite_failed.csv"


def _default_results_csv() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _REPO_ROOT / "backups" / f"dm_affected_gap_invite_{stamp}.csv"


def _gap_csv_path(club_key: str) -> Path:
    filename = GAP_CSV_BY_CLUB.get(club_key)
    if filename is None:
        raise SystemExit(f"Unknown club_key: {club_key!r}")
    return _REPO_ROOT / filename


def _default_input_paths(*, club_key_filter: str | None) -> list[Path]:
    if club_key_filter:
        return [_gap_csv_path(club_key_filter)]
    return [_gap_csv_path(key) for key in GAP_CLUB_ORDER]


def _load_gap_targets(
    paths: list[Path],
    *,
    club_key_filter: str | None,
    chat_id_filter: int | None,
) -> list:
    targets = []
    for path in paths:
        targets.extend(
            _load_targets_from_csv(
                path,
                club_key_filter=club_key_filter,
                chat_id_filter=chat_id_filter,
            )
        )
    return targets


def _failed_row_dict(result: DmResult, *, failed_at: str) -> dict[str, str]:
    return {
        "player_username": result.player_username or "",
        "invite_link": result.invite_link or "",
        "telegram_chat_id": str(result.telegram_chat_id),
        "gc_title": result.gc_title,
        "club_key": result.club_key,
        "player_telegram_user_id": str(result.player_telegram_user_id or ""),
        "status": result.status,
        "error": result.error or "",
        "failed_at": failed_at,
    }


def _load_failed_csv(path: Path) -> dict[int, dict[str, str]]:
    if not path.is_file():
        return {}
    merged: dict[int, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw = (row.get("telegram_chat_id") or "").strip()
            try:
                chat_id = int(raw)
            except ValueError:
                continue
            merged[chat_id] = {k: row.get(k, "") or "" for k in FAILED_CSV_FIELDS_WITH_AT}
    return merged


def _merge_failed_csv(path: Path, new_failures: list[DmResult]) -> int:
    existing = _load_failed_csv(path)
    now = datetime.now(timezone.utc).isoformat()
    for result in new_failures:
        existing[int(result.telegram_chat_id)] = _failed_row_dict(result, failed_at=now)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FAILED_CSV_FIELDS_WITH_AT)
        writer.writeheader()
        for chat_id in sorted(existing.keys()):
            writer.writerow(existing[chat_id])
    return len(existing)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-csv",
        type=Path,
        action="append",
        dest="input_csvs",
        help="Gap CSV path(s). Default: gc_affected_not_covered_<club>.csv for selected clubs.",
    )
    parser.add_argument(
        "--club-key",
        choices=CLUB_KEYS,
        help="Limit to one /gc MTProto club profile.",
    )
    parser.add_argument("--chat-id", type=int, help="Limit to one telegram chat id.")
    parser.add_argument(
        "--limit",
        type=int,
        help="Send/would-send at most N players this run (CSV order, after skips).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Send DMs and optionally export links (default: dry-run).",
    )
    parser.add_argument(
        "--export-invite-links",
        action="store_true",
        help="With --apply, export a fresh invite link via Telethon before DM.",
    )
    parser.add_argument(
        "--update-invite-links",
        action="store_true",
        help="With --apply --export-invite-links, upsert invite_link to Postgres.",
    )
    parser.add_argument(
        "--tracker-csv",
        type=Path,
        default=_default_tracker_csv(),
        help="Append-only log of who was DM'd (default: backups/dm_affected_gap_invite_tracker.csv).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore tracker and resend even if already dm_sent.",
    )
    parser.add_argument(
        "--dm-delay",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="Pause between groups per club session (default: 2).",
    )
    parser.add_argument(
        "--failed-csv-out",
        type=Path,
        default=_default_failed_csv(),
        help="Persistent merged failed-DM CSV (default: backups/dm_affected_gap_invite_failed.csv).",
    )
    parser.add_argument(
        "--csv-out",
        type=Path,
        help="Write per-group results here (default: backups/dm_affected_gap_invite_<ts>.csv).",
    )
    parser.add_argument("--json", action="store_true", help="JSON summary to stdout.")
    parser.add_argument("--quiet", action="store_true", help="Only warnings/errors on stderr.")
    args = parser.parse_args()

    if not args.json:
        _configure_logging(quiet=args.quiet)

    input_paths = args.input_csvs or _default_input_paths(club_key_filter=args.club_key)
    for path in input_paths:
        if not path.is_file():
            raise SystemExit(f"Gap CSV not found: {path}")

    targets = _load_gap_targets(
        input_paths,
        club_key_filter=args.club_key,
        chat_id_filter=args.chat_id,
    )
    if not targets:
        raise SystemExit("No targets matched filters.")

    tracker = DmTracker(args.tracker_csv.resolve())
    tracker.load()
    use_tracker = not bool(args.force)

    summary, results = asyncio.run(
        _run(
            targets=targets,
            apply=bool(args.apply),
            export_invite_links=bool(args.export_invite_links),
            update_invite_links=bool(args.update_invite_links),
            use_tracker=use_tracker,
            tracker=tracker,
            dm_delay_seconds=max(0.0, float(args.dm_delay)),
            send_limit=args.limit,
        )
    )

    csv_path = args.csv_out or _default_results_csv()
    failed_csv_path = args.failed_csv_out.resolve()
    _write_results_csv(csv_path, results)
    failed_rows = _failed_dm_results(results)
    merged_failed_total = _merge_failed_csv(failed_csv_path, failed_rows)
    summary.failed_csv_path = str(failed_csv_path)
    summary.failed_csv_rows = merged_failed_total

    if args.json:
        print(
            json.dumps(
                {
                    "summary": asdict(summary),
                    "results_csv": str(csv_path),
                    "failed_csv": str(failed_csv_path),
                    "failed_this_run": len(failed_rows),
                    "failed_merged_total": merged_failed_total,
                    "failed_rows": [asdict(r) for r in failed_rows],
                    "groups": [asdict(r) for r in results],
                },
                indent=2,
            )
        )
    else:
        _print_human(summary, results, csv_path, failed_csv_path)
        if failed_rows:
            print(
                f"\nMerged {len(failed_rows)} failure(s) from this run into "
                f"{failed_csv_path} ({merged_failed_total} total rows)"
            )

    if summary.errors and args.apply:
        sys.exit(2)


if __name__ == "__main__":
    main()
