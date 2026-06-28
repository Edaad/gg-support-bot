"""Debug CLI for inactive group outreach scan (local / single-group dry-run).

Production path is the worker job (``GC_INACTIVE_OUTREACH_SCAN_ENABLED=true``).
Uses a dedicated MTProto session — stop the worker or use a test club session.

Usage:
  python scripts/run_inactive_group_outreach_scan.py --club-key round_table --dry-run
  python scripts/run_inactive_group_outreach_scan.py --club-key round_table --chat-id -100123
  python scripts/run_inactive_group_outreach_scan.py --row-id 42 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
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

logger = logging.getLogger("run_inactive_group_outreach_scan")

CLUB_KEYS = ("round_table", "creator_club", "clubgto")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run inactive group outreach scan locally.")
    parser.add_argument("--club-key", choices=CLUB_KEYS, default="round_table")
    parser.add_argument("--chat-id", type=int, default=None, help="Scan one supergroup chat id.")
    parser.add_argument("--row-id", type=int, default=None, help="Scan one DB outreach row by id.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print scan result JSON only (default unless --apply).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist scan fields to inactive_group_outreach_rows.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


async def _run_scan(args: argparse.Namespace) -> int:
    from club_gc_settings import CLUB_GC_CONFIG, get_inactive_outreach_history_limit
    from bot.services.inactive_group_outreach import (
        OutreachScanRow,
        persist_row_scan,
        scan_outreach_row,
    )
    from bot.services.migration_group_readd import load_player_rows_by_chat
    from bot.services.mtproto_group_activity import resolve_exclude_user_ids
    from bot.services.mtproto_group_create import is_client_authorized, make_client
    from db.connection import get_db
    from db.models import InactiveGroupOutreachRow

    dry_run = not args.apply

    if args.row_id is not None:
        with get_db() as session:
            model = session.get(InactiveGroupOutreachRow, int(args.row_id))
            if model is None:
                raise SystemExit(f"No outreach row id={args.row_id}")
            row = OutreachScanRow(
                id=int(model.id),
                club_key=str(model.club_key),
                telegram_chat_id=int(model.telegram_chat_id),
                group_title=str(model.group_title),
                legacy_chat_id=int(model.legacy_chat_id)
                if model.legacy_chat_id is not None
                else None,
                gg_player_id=str(model.gg_player_id) if model.gg_player_id else None,
            )
        club_key = row.club_key
    elif args.chat_id is not None:
        club_key = args.club_key
        row = OutreachScanRow(
            id=0,
            club_key=club_key,
            telegram_chat_id=int(args.chat_id),
            group_title=f"(cli chat {args.chat_id})",
            legacy_chat_id=None,
            gg_player_id=None,
        )
    else:
        raise SystemExit("Provide --chat-id or --row-id")

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
    try:
        if not await client.is_user_authorized():
            raise SystemExit(f"MTProto session not authorized for {club_key!r}.")

        me = await client.get_me()
        self_id = int(me.id) if me and getattr(me, "id", None) else None
        exclude_user_ids = await resolve_exclude_user_ids(client, cfg, self_id or 0)
        history_limit = get_inactive_outreach_history_limit()
        player_map = load_player_rows_by_chat({row.telegram_chat_id})

        fields = await scan_outreach_row(
            client,
            cfg,
            row,
            self_id=self_id,
            exclude_user_ids=exclude_user_ids,
            history_limit=history_limit,
            player_map=player_map,
        )
    finally:
        await client.disconnect()

    def _json_default(value):
        if hasattr(value, "isoformat"):
            return value.isoformat()
        raise TypeError(type(value).__name__)

    print(json.dumps(fields, indent=2, default=_json_default))

    if dry_run:
        print("(dry-run; pass --apply to persist)", file=sys.stderr)
        return 0

    if row.id <= 0:
        raise SystemExit("--apply requires --row-id (no DB row for bare --chat-id)")

    persist_row_scan(row.id, fields)
    print(f"Updated outreach row id={row.id}", file=sys.stderr)
    return 0


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    return asyncio.run(_run_scan(args))


if __name__ == "__main__":
    raise SystemExit(main())
