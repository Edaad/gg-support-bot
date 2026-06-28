"""Debug CLI for inactive outreach DM send (local / single-row).

Production path: /sendinactive in bot DM + worker job (``GC_INACTIVE_OUTREACH_DM_ENABLED=true``).
Uses the club MTProto listener session — do not run while the worker holds the same session.

Usage:
  python scripts/run_inactive_outreach_dm.py --club-key round_table --row-id 42 --message "Hi" --dry-run
  python scripts/run_inactive_outreach_dm.py --club-key round_table --chat-id -100123 --message "Hi" --apply
"""

from __future__ import annotations

import argparse
import asyncio
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

logger = logging.getLogger("run_inactive_outreach_dm")

CLUB_KEYS = ("round_table", "creator_club", "clubgto")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send inactive outreach DM locally.")
    parser.add_argument("--club-key", choices=CLUB_KEYS, default="round_table")
    parser.add_argument("--row-id", type=int, default=None)
    parser.add_argument("--chat-id", type=int, default=None)
    parser.add_argument("--message", required=True, help="Outreach DM body")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve target only; do not send (default unless --apply).",
    )
    parser.add_argument("--apply", action="store_true", help="Send DM via MTProto.")
    parser.add_argument("--staged-by-user-id", type=int, default=0)
    return parser.parse_args()


def _resolve_row_id(club_key: str, row_id: int | None, chat_id: int | None) -> int | None:
    from db.connection import get_db
    from db.models import InactiveGroupOutreachRow

    if row_id is not None:
        return int(row_id)
    if chat_id is None:
        return None
    with get_db() as session:
        row = (
            session.query(InactiveGroupOutreachRow)
            .filter_by(club_key=club_key, telegram_chat_id=int(chat_id))
            .order_by(InactiveGroupOutreachRow.id.desc())
            .first()
        )
        return int(row.id) if row else None


async def _run() -> int:
    from club_gc_settings import CLUB_GC_CONFIG
    from bot.services.inactive_group_outreach_dm import DmOutreachRow, _send_one_dm
    from bot.services.mtproto_group_create import make_client
    from db.connection import get_db
    from db.models import InactiveGroupOutreachRow

    args = _parse_args()
    apply = bool(args.apply)
    if not apply and not args.dry_run:
        apply = False

    row_pk = _resolve_row_id(args.club_key, args.row_id, args.chat_id)
    if row_pk is None:
        print("No outreach row found (use --row-id or --chat-id).", file=sys.stderr)
        return 1

    with get_db() as session:
        row = session.get(InactiveGroupOutreachRow, row_pk)
        if row is None:
            print(f"Row id={row_pk} not found.", file=sys.stderr)
            return 1
        dm_row = DmOutreachRow(
            id=int(row.id),
            club_key=str(row.club_key),
            telegram_chat_id=int(row.telegram_chat_id),
            group_title=str(row.group_title),
            player_telegram_user_id=int(row.player_telegram_user_id or 0),
            player_username=row.player_username,
            player_display_name=row.player_display_name,
        )

    if not dm_row.player_telegram_user_id:
        print("Row has no player_telegram_user_id.", file=sys.stderr)
        return 1

    print(
        f"target row={dm_row.id} chat={dm_row.telegram_chat_id} "
        f"player={dm_row.player_telegram_user_id} title={dm_row.group_title!r}"
    )
    print(f"message ({len(args.message)} chars): {args.message[:200]}")

    if not apply:
        print("Dry-run only (pass --apply to send).")
        return 0

    cfg = CLUB_GC_CONFIG.get(args.club_key)
    if cfg is None:
        print(f"Unknown club_key {args.club_key}", file=sys.stderr)
        return 1

    client = make_client(cfg)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            print("MTProto session not authorized.", file=sys.stderr)
            return 1
        ok, err = await _send_one_dm(client, cfg, dm_row, args.message.strip())
        if ok:
            print("DM sent.")
            return 0
        print(f"DM failed: {err}", file=sys.stderr)
        return 1
    finally:
        await client.disconnect()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
