"""Resolve players for staged inactive groups and classify DM reachability.

Entity resolution order (same as inactive outreach worker):
  1. ``support_group_chats.player_telegram_user_id`` on file → ``get_entity`` + alive check
  2. Message history walk (supergroup + legacy basic group)

DM reachability is inferred without sending a message:
  - ``likely_dm`` — account alive + in club MTProto contacts (or mutual)
  - ``possible_dm`` — account alive, not in contacts (privacy may still block)
  - ``unlikely_dm`` — deleted / not found / no player id

Does **not** send DMs. Pass ``--apply`` to persist player fields on outreach rows.

Operational: disable worker MTProto before running against production session
(``GC_MTPROTO_ENABLED=false`` + restart worker).

Usage:
  python scripts/check_staged_inactive_dm_reachability.py --club-key round_table --limit 1
  python scripts/check_staged_inactive_dm_reachability.py --club-key round_table --chat-id -100123
  python scripts/check_staged_inactive_dm_reachability.py --club-key round_table --apply --delay 0.3
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
from typing import Any, Literal

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

logger = logging.getLogger("check_staged_inactive_dm_reachability")

CLUB_KEYS = ("round_table", "creator_club", "clubgto")

DmReachability = Literal[
    "no_player",
    "account_deleted",
    "account_not_found",
    "likely_dm",
    "possible_dm",
    "error",
]

CSV_FIELDS = (
    "row_id",
    "club_key",
    "telegram_chat_id",
    "group_title",
    "gg_player_id",
    "stored_player_id",
    "player_telegram_user_id",
    "player_username",
    "player_display_name",
    "player_source",
    "account_check",
    "entity_resolvable",
    "in_contacts",
    "mutual_contact",
    "dm_reachability",
    "resolve_error",
    "checked_at_utc",
)


@dataclass(frozen=True)
class StagedRow:
    id: int
    club_key: str
    telegram_chat_id: int
    group_title: str
    gg_player_id: str | None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve players on staged inactive groups and classify DM reachability.",
    )
    parser.add_argument("--club-key", choices=CLUB_KEYS, default="round_table")
    parser.add_argument("--row-id", type=int, default=None)
    parser.add_argument("--chat-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip", type=int, default=0)
    parser.add_argument(
        "--skip-if-resolved",
        action="store_true",
        help="Skip staged rows that already have account_check set (resume helper).",
    )
    parser.add_argument("--delay", type=float, default=0.25, help="Seconds between rows.")
    parser.add_argument("--apply", action="store_true", help="Persist player fields to DB.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Result CSV (default: backups/staged_dm_reachability_<ts>.csv).",
    )
    parser.add_argument(
        "--export-from-db",
        action="store_true",
        help="Write CSV from Postgres only (no Telethon). Uses entity_resolvable for reachability.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def _load_staged_rows(args: argparse.Namespace) -> list[StagedRow]:
    from db.connection import get_db
    from db.models import InactiveGroupOutreachRow

    with get_db() as session:
        query = session.query(InactiveGroupOutreachRow).filter(
            InactiveGroupOutreachRow.stage_status == "staged",
            InactiveGroupOutreachRow.club_key == args.club_key,
        )
        if args.row_id is not None:
            query = query.filter(InactiveGroupOutreachRow.id == int(args.row_id))
        if args.chat_id is not None:
            query = query.filter(
                InactiveGroupOutreachRow.telegram_chat_id == int(args.chat_id)
            )
        if args.skip_if_resolved:
            query = query.filter(InactiveGroupOutreachRow.account_check.is_(None))
        query = query.order_by(InactiveGroupOutreachRow.id.asc())
        if args.skip:
            query = query.offset(int(args.skip))
        if args.limit is not None:
            query = query.limit(max(1, int(args.limit)))
        models = query.all()
        return [
            StagedRow(
                id=int(r.id),
                club_key=str(r.club_key),
                telegram_chat_id=int(r.telegram_chat_id),
                group_title=str(r.group_title),
                gg_player_id=str(r.gg_player_id) if r.gg_player_id else None,
            )
            for r in models
        ]


def classify_dm_reachability(
    resolution: dict[str, Any],
    *,
    in_contacts: bool | None,
    mutual_contact: bool | None,
) -> DmReachability:
    player_id = resolution.get("player_telegram_user_id")
    if player_id is None:
        return "no_player"

    account_check = resolution.get("account_check")
    if account_check == "deleted":
        return "account_deleted"
    if not resolution.get("entity_resolvable"):
        return "account_not_found"

    if mutual_contact or in_contacts:
        return "likely_dm"
    return "possible_dm"


async def _contact_flags(client, player_id: int) -> tuple[bool | None, bool | None]:
    from bot.services.migration_group_readd import (
        call_with_flood_retry,
        is_entity_resolution_error,
    )

    try:
        user = await call_with_flood_retry(
            lambda: client.get_entity(int(player_id)),
            label=f"dm_reachability:contact_flags:{player_id}",
        )
    except Exception as exc:
        if is_entity_resolution_error(exc):
            return None, None
        raise

    return bool(getattr(user, "contact", False)), bool(getattr(user, "mutual_contact", False))


async def _check_one_row(
    client,
    cfg,
    row: StagedRow,
    *,
    self_id: int | None,
    history_limit: int,
    player_map: dict[int, tuple[int | None, str | None, str | None]],
) -> tuple[dict[str, str], dict[str, Any]]:
    from bot.services.inactive_group_outreach import (
        OutreachScanRow,
        _resolve_player_for_row,
    )

    stored = player_map.get(int(row.telegram_chat_id))
    stored_player_id = str(stored[0]) if stored and stored[0] is not None else ""
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    resolve_error = ""

    scan_row = OutreachScanRow(
        id=row.id,
        club_key=row.club_key,
        telegram_chat_id=row.telegram_chat_id,
        group_title=row.group_title,
        legacy_chat_id=None,
        gg_player_id=row.gg_player_id,
    )

    try:
        resolution = await _resolve_player_for_row(
            client,
            cfg,
            scan_row,
            self_id=self_id,
            history_limit=history_limit,
            player_map=player_map,
        )
    except Exception as exc:
        logger.exception("resolve failed row_id=%s chat=%s", row.id, row.telegram_chat_id)
        resolution = {
            "player_telegram_user_id": None,
            "player_username": None,
            "player_display_name": None,
            "player_source": "none",
            "account_check": None,
            "entity_resolvable": False,
        }
        resolve_error = type(exc).__name__

    in_contacts: bool | None = None
    mutual_contact: bool | None = None
    player_id = resolution.get("player_telegram_user_id")
    if player_id is not None and resolution.get("entity_resolvable"):
        in_contacts, mutual_contact = await _contact_flags(client, int(player_id))

    dm_reachability = classify_dm_reachability(
        resolution,
        in_contacts=in_contacts,
        mutual_contact=mutual_contact,
    )

    out = {
        "row_id": str(row.id),
        "club_key": row.club_key,
        "telegram_chat_id": str(row.telegram_chat_id),
        "group_title": row.group_title,
        "gg_player_id": row.gg_player_id or "",
        "stored_player_id": stored_player_id,
        "player_telegram_user_id": str(player_id or ""),
        "player_username": str(resolution.get("player_username") or ""),
        "player_display_name": str(resolution.get("player_display_name") or ""),
        "player_source": str(resolution.get("player_source") or ""),
        "account_check": str(resolution.get("account_check") or ""),
        "entity_resolvable": "true" if resolution.get("entity_resolvable") else "false",
        "in_contacts": "" if in_contacts is None else ("true" if in_contacts else "false"),
        "mutual_contact": ""
        if mutual_contact is None
        else ("true" if mutual_contact else "false"),
        "dm_reachability": dm_reachability,
        "resolve_error": resolve_error,
        "checked_at_utc": checked_at,
    }

    return out, resolution


async def _run(args: argparse.Namespace) -> int:
    from club_gc_settings import CLUB_GC_CONFIG, get_inactive_outreach_history_limit
    from bot.services.inactive_group_outreach import persist_row_scan
    from bot.services.migration_group_readd import load_player_rows_by_chat
    from bot.services.mtproto_group_create import is_client_authorized, make_client

    rows = _load_staged_rows(args)
    if not rows:
        print("No staged rows matched.", file=sys.stderr)
        return 1

    cfg = CLUB_GC_CONFIG.get(args.club_key)
    if cfg is None:
        raise SystemExit(f"Unknown club_key: {args.club_key!r}")

    if not await is_client_authorized(cfg):
        raise SystemExit(
            f"MTProto session not authorized for {args.club_key!r}. "
            "Complete Dashboard → Telegram login first."
        )

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = args.output or (_REPO_ROOT / "backups" / f"staged_dm_reachability_{ts}.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    client = make_client(cfg)
    await client.connect()
    result_rows: list[dict[str, str]] = []
    counts: dict[str, int] = {}

    try:
        if not await client.is_user_authorized():
            raise SystemExit(f"MTProto session not authorized for {args.club_key!r}.")

        me = await client.get_me()
        self_id = int(me.id) if me and getattr(me, "id", None) else None
        history_limit = get_inactive_outreach_history_limit()
        chat_ids = {r.telegram_chat_id for r in rows}
        player_map = load_player_rows_by_chat(chat_ids)

        with out_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
            writer.writeheader()

            for idx, row in enumerate(rows, start=1):
                out, resolution = await _check_one_row(
                    client,
                    cfg,
                    row,
                    self_id=self_id,
                    history_limit=history_limit,
                    player_map=player_map,
                )
                result_rows.append(out)
                writer.writerow(out)
                csv_file.flush()
                counts[out["dm_reachability"]] = counts.get(out["dm_reachability"], 0) + 1

                if args.apply:
                    persist_row_scan(
                        row.id,
                        {
                            "player_telegram_user_id": resolution.get("player_telegram_user_id"),
                            "player_username": resolution.get("player_username"),
                            "player_display_name": resolution.get("player_display_name"),
                            "player_source": resolution.get("player_source"),
                            "account_check": resolution.get("account_check"),
                            "entity_resolvable": bool(resolution.get("entity_resolvable")),
                        },
                    )

                if args.verbose or idx % 25 == 0 or idx == len(rows):
                    logger.info(
                        "[%d/%d] row=%s chat=%s -> %s",
                        idx,
                        len(rows),
                        row.id,
                        row.telegram_chat_id,
                        out["dm_reachability"],
                    )

                if args.delay > 0 and idx < len(rows):
                    await asyncio.sleep(args.delay)
    finally:
        await client.disconnect()

    mode = "apply" if args.apply else "dry-run"
    print(f"{mode}: checked={len(rows)}")
    for key in (
        "likely_dm",
        "possible_dm",
        "account_not_found",
        "account_deleted",
        "no_player",
        "error",
    ):
        if counts.get(key):
            print(f"  {key}: {counts[key]}")
    print(f"Wrote {out_path}")
    if not args.apply:
        print("Pass --apply to persist player fields on outreach rows.", file=sys.stderr)
    return 0


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
