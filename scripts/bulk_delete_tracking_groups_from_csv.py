"""Bulk erase or leave tracking-format support groups from a least-active CSV.

Reads a CSV produced by ``list_least_active_telegram_groups.py``, filters rows whose
title matches ``SHORTHAND / xxxx-xxxx / player`` (via ``parse_tracking_title``), and
runs the same Telegram erase logic as MTProto ``/delete confirm``. When delete fails
(not creator, no admin rights, etc.), falls back to leaving the chat so the MTProto
account still frees group quota.

Does **not** remove Postgres rows (``groups``, ``player_details``, ``support_group_chats``).

Environment: TG_API_ID, TG_API_HASH, Postgres-backed MTProto sessions (same as other
Telethon scripts).

Operational — disable worker MTProto before running against production:

  heroku config:set GC_MTPROTO_ENABLED=false -a gg-support-bot-2025
  heroku restart worker -a gg-support-bot-2025

Re-enable when finished:

  heroku config:unset GC_MTPROTO_ENABLED -a gg-support-bot-2025
  heroku restart worker -a gg-support-bot-2025

Usage:

  # Preview filtered targets (dry-run; writes preview CSV)
  python scripts/bulk_delete_tracking_groups_from_csv.py \\
    --csv backups/least_active_groups_round_table_20260620_212205.csv \\
    --club-key round_table

  # Single-group proof (run this before any batch)
  python scripts/bulk_delete_tracking_groups_from_csv.py \\
    --csv backups/least_active_groups_round_table_20260620_212205.csv \\
    --club-key round_table --chat-id -4794237573 --apply

  # Batch after one-group success
  python scripts/bulk_delete_tracking_groups_from_csv.py \\
    --csv backups/least_active_groups_round_table_20260620_212205.csv \\
    --club-key round_table --apply --limit 50 --delay 3

  # Resume after interruption (skip first N already-processed targets)
  python scripts/bulk_delete_tracking_groups_from_csv.py \\
    --csv backups/least_active_groups_round_table_20260620_212205.csv \\
    --club-key round_table --apply --skip 266 --delay 3
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if sys.version_info < (3, 10):
    raise SystemExit(
        f"Python 3.10+ required (this interpreter is {sys.version.split()[0]}). "
        "Use pyenv/python3.11 or recreate the venv."
    )

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

logger = logging.getLogger("bulk_delete_tracking_groups_from_csv")

CLUB_KEYS = ("round_table", "creator_club", "clubgto")

PREVIEW_COLUMNS = (
    "rank",
    "title",
    "chat_id",
    "kind",
    "inactive_label",
    "duplicate_title",
    "shorthand",
    "gg_player_id",
)

RESULT_COLUMNS = (
    "title",
    "chat_id",
    "kind",
    "shorthand",
    "gg_player_id",
    "action",
    "detail",
    "processed_at_utc",
)


@dataclass(frozen=True)
class TargetRow:
    rank: str
    title: str
    chat_id: int
    kind: str
    inactive_label: str
    duplicate_title: bool
    shorthand: str
    gg_player_id: str


@dataclass(frozen=True)
class ProcessResult:
    title: str
    chat_id: int
    kind: str
    shorthand: str
    gg_player_id: str
    action: str
    detail: str


def _parse_bool_yes(value: str) -> bool:
    return str(value or "").strip().lower() == "yes"


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _filter_targets(
    rows: list[dict[str, str]],
    *,
    allowed_kinds: frozenset[str],
    include_duplicate_titles: bool,
    chat_id: int | None,
    limit: int | None,
) -> tuple[list[TargetRow], list[str]]:
    from bot.services.player_details import parse_tracking_title

    skipped: list[str] = []
    out: list[TargetRow] = []

    for row in rows:
        title = (row.get("title") or "").strip()
        parsed = parse_tracking_title(title)
        if parsed is None:
            continue

        shorthand, gg_player_id = parsed
        kind = (row.get("kind") or "").strip()
        if kind and kind not in allowed_kinds:
            skipped.append(f"kind filtered: {title} ({kind})")
            continue

        is_dup = _parse_bool_yes(row.get("duplicate_title", ""))
        if is_dup and not include_duplicate_titles:
            skipped.append(f"duplicate title skipped: {title}")
            continue

        try:
            cid = int(row.get("chat_id") or "")
        except ValueError:
            skipped.append(f"invalid chat_id: {title}")
            continue

        if chat_id is not None and cid != int(chat_id):
            continue

        out.append(
            TargetRow(
                rank=str(row.get("rank") or ""),
                title=title,
                chat_id=cid,
                kind=kind,
                inactive_label=str(row.get("inactive_label") or ""),
                duplicate_title=is_dup,
                shorthand=shorthand,
                gg_player_id=gg_player_id,
            )
        )

    if chat_id is not None and not out:
        raise SystemExit(f"chat_id {chat_id} not found in CSV after filters.")

    if limit is not None:
        out = out[:limit]

    return out, skipped


def _default_preview_path() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _REPO_ROOT / "backups" / f"bulk_delete_tracking_groups_preview_{ts}.csv"


def _default_results_path() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _REPO_ROOT / "backups" / f"bulk_delete_tracking_groups_results_{ts}.csv"


def _write_preview(path: Path, targets: list[TargetRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PREVIEW_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in targets:
            writer.writerow(
                {
                    "rank": row.rank,
                    "title": row.title,
                    "chat_id": row.chat_id,
                    "kind": row.kind,
                    "inactive_label": row.inactive_label,
                    "duplicate_title": "yes" if row.duplicate_title else "no",
                    "shorthand": row.shorthand,
                    "gg_player_id": row.gg_player_id,
                }
            )


def _write_results(path: Path, results: list[ProcessResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in results:
            writer.writerow(
                {
                    "title": row.title,
                    "chat_id": row.chat_id,
                    "kind": row.kind,
                    "shorthand": row.shorthand,
                    "gg_player_id": row.gg_player_id,
                    "action": row.action,
                    "detail": row.detail,
                    "processed_at_utc": now,
                }
            )


async def _resolve_entity(client: Any, chat_id: int):
    from notification.chat_id import telegram_chat_id_variants

    last_exc: Exception | None = None
    for cid in telegram_chat_id_variants(int(chat_id)):
        try:
            entity = await client.get_entity(int(cid))
            return entity, int(cid)
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(
        f"Could not resolve chat_id {chat_id} ({type(last_exc).__name__ if last_exc else 'unknown'})"
    )


async def _leave_group(client: Any, entity) -> str | None:
    try:
        await client.delete_dialog(entity)
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


async def _process_one(
    client: Any,
    cfg,
    target: TargetRow,
) -> ProcessResult:
    from bot.services.mtproto_group_create import get_mtproto_lock
    from bot.services.mtproto_group_delete import erase_group_chat

    async with get_mtproto_lock(cfg.club_key):
        try:
            entity, resolved_id = await _resolve_entity(client, target.chat_id)
        except Exception as exc:
            return ProcessResult(
                title=target.title,
                chat_id=target.chat_id,
                kind=target.kind,
                shorthand=target.shorthand,
                gg_player_id=target.gg_player_id,
                action="error",
                detail=f"resolve failed: {exc}",
            )

        err = await erase_group_chat(client, cfg=cfg, chat_id=resolved_id)
        if err is None:
            return ProcessResult(
                title=target.title,
                chat_id=target.chat_id,
                kind=target.kind,
                shorthand=target.shorthand,
                gg_player_id=target.gg_player_id,
                action="deleted",
                detail="",
            )

        leave_err = await _leave_group(client, entity)
        if leave_err is None:
            return ProcessResult(
                title=target.title,
                chat_id=target.chat_id,
                kind=target.kind,
                shorthand=target.shorthand,
                gg_player_id=target.gg_player_id,
                action="left",
                detail=err,
            )

        return ProcessResult(
            title=target.title,
            chat_id=target.chat_id,
            kind=target.kind,
            shorthand=target.shorthand,
            gg_player_id=target.gg_player_id,
            action="error",
            detail=f"delete: {err}; leave: {leave_err}",
        )


async def _run_apply(
    club_key: str,
    targets: list[TargetRow],
    *,
    delay_sec: float,
    results_path: Path,
    skip: int = 0,
    total_targets: int | None = None,
) -> list[ProcessResult]:
    from club_gc_settings import CLUB_GC_CONFIG
    from bot.services.mtproto_group_create import is_client_authorized, make_client

    cfg = CLUB_GC_CONFIG.get(club_key)
    if cfg is None:
        raise SystemExit(f"Unknown club_key: {club_key!r}")

    if not await is_client_authorized(cfg):
        raise SystemExit(
            f"MTProto session not authorized for {club_key!r}. "
            "Complete Dashboard → Telegram login first."
        )

    client = make_client(cfg)
    await client.connect()
    results: list[ProcessResult] = []
    try:
        if not await client.is_user_authorized():
            raise SystemExit(f"MTProto session not authorized for {club_key!r}.")

        total = total_targets if total_targets is not None else len(targets)
        for idx, target in enumerate(targets, start=1):
            logger.info(
                "[%d/%d] processing chat_id=%s title=%r",
                skip + idx,
                total,
                target.chat_id,
                target.title,
            )
            result = await _process_one(client, cfg, target)
            results.append(result)
            logger.info(
                "  → action=%s detail=%s",
                result.action,
                result.detail[:200] if result.detail else "(ok)",
            )
            if idx < len(targets) and delay_sec > 0:
                await asyncio.sleep(delay_sec)
    finally:
        await client.disconnect()

    _write_results(results_path, results)
    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bulk /delete confirm for tracking-format groups listed in a CSV."
    )
    parser.add_argument(
        "--csv",
        type=Path,
        required=True,
        help="Input CSV from list_least_active_telegram_groups.py",
    )
    parser.add_argument(
        "--club-key",
        choices=CLUB_KEYS,
        default="round_table",
        help="Club MTProto session to use (default: round_table).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform Telegram delete/leave (default: dry-run preview only).",
    )
    parser.add_argument(
        "--chat-id",
        type=int,
        default=None,
        help="Process a single chat id from the CSV (test one group first).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of filtered targets to process.",
    )
    parser.add_argument(
        "--skip",
        type=int,
        default=0,
        help="Skip the first N filtered targets (resume after interruption).",
    )
    parser.add_argument(
        "--kinds",
        default="basic_group,megagroup,group,channel",
        help="Comma-separated dialog kinds to include (default: all group kinds).",
    )
    parser.add_argument(
        "--include-duplicate-titles",
        action="store_true",
        help="Include rows with duplicate_title=yes (stale basic group copies).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds to sleep between groups when --apply (default: 2).",
    )
    parser.add_argument(
        "--preview-output",
        type=Path,
        default=None,
        help="Preview CSV path for dry-run (default: backups/bulk_delete_tracking_groups_preview_<ts>.csv).",
    )
    parser.add_argument(
        "--results-output",
        type=Path,
        default=None,
        help="Results CSV path when --apply (default: backups/bulk_delete_tracking_groups_results_<ts>.csv).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log progress at INFO.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.csv.is_file():
        raise SystemExit(f"CSV not found: {args.csv}")

    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be >= 1")
    if args.skip < 0:
        raise SystemExit("--skip must be >= 0")
    if args.delay < 0:
        raise SystemExit("--delay must be >= 0")

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    allowed_kinds = frozenset(k.strip() for k in args.kinds.split(",") if k.strip())
    if not allowed_kinds:
        raise SystemExit("--kinds must list at least one kind.")

    rows = _load_csv_rows(args.csv)
    targets, skipped = _filter_targets(
        rows,
        allowed_kinds=allowed_kinds,
        include_duplicate_titles=args.include_duplicate_titles,
        chat_id=args.chat_id,
        limit=args.limit,
    )

    print(f"CSV rows: {len(rows)}")
    print(f"Targets after filter: {len(targets)}")
    if skipped:
        print(f"Skipped notes: {len(skipped)} (use --verbose to log each)")

    if not targets:
        print("No matching targets.", file=sys.stderr)
        return 1

    total_before_skip = len(targets)
    if args.skip:
        if args.skip >= total_before_skip:
            raise SystemExit(
                f"--skip {args.skip} >= target count {total_before_skip}; nothing left to process."
            )
        targets = targets[args.skip :]
        print(f"Skipping first {args.skip} targets; {len(targets)} remaining.")

    preview_path = args.preview_output or _default_preview_path()
    _write_preview(preview_path, targets)
    print(f"Preview written: {preview_path}")

    print("\nFirst 10 targets:")
    for row in targets[:10]:
        print(
            f"  {row.rank or '?'}. [{row.inactive_label}] {row.title} "
            f"({row.kind}, id={row.chat_id})"
        )

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to delete/leave these groups.")
        return 0

    results_path = args.results_output or _default_results_path()
    results = asyncio.run(
        _run_apply(
            args.club_key,
            targets,
            delay_sec=args.delay,
            results_path=results_path,
            skip=args.skip,
            total_targets=total_before_skip,
        )
    )

    counts: dict[str, int] = {}
    for row in results:
        counts[row.action] = counts.get(row.action, 0) + 1

    print(f"\nResults written: {results_path}")
    print("Summary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0 if counts.get("error", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
