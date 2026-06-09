"""Find duplicate support group chats (same GG player ID within one club) via Telethon.

Scans all group/supergroup dialogs visible to a club MTProto account, parses titles in
the tracking format ``SHORTHAND / GGPLAYERID / …``, and reports sets where the same
``(club_id, gg_player_id)`` appears on two or more distinct chat IDs.

Groups with non-tracking titles (e.g. ``/gc`` megagroups ``RT / / @player``) are skipped.

Environment:
  TG_API_ID, TG_API_HASH — required (https://my.telegram.org/apps)
  DATABASE_URL — required to resolve club shorthand → clubs.id
  GC_MTPROTO_DB_SESSIONS — when true (default), loads Telethon StringSession from Postgres

Operational:
  Do not run while the bot worker holds the same club Telethon session; only one
  connection per session. On Heroku set ``GC_MTPROTO_ENABLED=false`` (or
  ``GC_DM_GC_LISTENER_ENABLED=false``) and restart the worker before running locally.

Requires Python 3.10+ (see repo ``.python-version``). Prefer ``python3.11`` if ``.venv`` was built with 3.9.

Usage:
  DATABASE_URL=... TG_API_ID=... TG_API_HASH=... \\
    python3.11 scripts/find_duplicate_player_groups.py --club-key round_table

  python scripts/find_duplicate_player_groups.py --club-key creator_club --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

if sys.version_info < (3, 10):
    raise SystemExit(
        f"Python 3.10+ required (this interpreter is {sys.version.split()[0]}). "
        "Use pyenv/python3.11 or recreate the venv: "
        "python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    )
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

CLUB_KEYS = ("round_table", "creator_club", "clubgto")


@dataclass(frozen=True)
class GroupRow:
    chat_id: int
    title: str
    gg_player_id: str
    club_id: int
    shorthand: str
    mtproto_club_key: str


@dataclass(frozen=True)
class ScanSummary:
    mtproto_club_key: str
    club_display_name: str
    dialogs_scanned: int
    group_dialogs: int
    parseable: int
    unparseable_title: int
    unknown_shorthand: int
    duplicate_sets: int


def _is_group_dialog(dialog) -> bool:
    if dialog.is_group:
        return True
    if dialog.is_channel:
        entity = dialog.entity
        return bool(getattr(entity, "megagroup", False))
    return False


def _club_name_for_id(club_id: int) -> str | None:
    from sqlalchemy import text

    from db.connection import get_db

    with get_db() as session:
        row = session.execute(
            text("SELECT name FROM clubs WHERE id = :id LIMIT 1"),
            {"id": int(club_id)},
        ).fetchone()
        return row[0] if row else None


async def _scan(club_key: str) -> tuple[ScanSummary, list[dict[str, Any]]]:
    from club_gc_settings import CLUB_GC_CONFIG
    from bot.services.mtproto_group_create import is_client_authorized, make_client
    from bot.services.player_details import (
        parse_tracking_title,
        resolve_club_id_from_shorthand,
    )
    from db.connection import init_engine

    cfg = CLUB_GC_CONFIG.get(club_key)
    if not cfg:
        raise SystemExit(f"Unknown club_key: {club_key!r}")

    init_engine()

    if not await is_client_authorized(cfg):
        raise SystemExit(
            "Telethon session is not authorized for this club. "
            "Log in via the dashboard MTProto flow or:\n"
            f"  python scripts/mtproto_login_cli.py --club-key {club_key}"
        )

    buckets: dict[tuple[int, str], dict[int, GroupRow]] = defaultdict(dict)
    dialogs_scanned = 0
    group_dialogs = 0
    parseable = 0
    unparseable_title = 0
    unknown_shorthand = 0

    client = make_client(cfg)
    await client.connect()
    try:
        async for dialog in client.iter_dialogs():
            dialogs_scanned += 1
            if not _is_group_dialog(dialog):
                continue
            if getattr(dialog.entity, "left", False):
                continue
            group_dialogs += 1

            title = (dialog.title or dialog.name or "").strip()
            parsed = parse_tracking_title(title)
            if not parsed:
                unparseable_title += 1
                continue

            shorthand, gg_player_id = parsed
            club_id = resolve_club_id_from_shorthand(shorthand)
            if club_id is None:
                unknown_shorthand += 1
                continue

            parseable += 1
            chat_id = int(dialog.id)
            row = GroupRow(
                chat_id=chat_id,
                title=title,
                gg_player_id=gg_player_id,
                club_id=int(club_id),
                shorthand=shorthand,
                mtproto_club_key=club_key,
            )
            buckets[(row.club_id, row.gg_player_id)][chat_id] = row
    finally:
        await client.disconnect()

    duplicate_payload: list[dict[str, Any]] = []
    for (club_id, gg_player_id), by_chat in sorted(
        buckets.items(), key=lambda x: (x[0][0], x[0][1])
    ):
        if len(by_chat) < 2:
            continue
        club_name = _club_name_for_id(club_id)
        chats = [
            {
                "chat_id": r.chat_id,
                "title": r.title,
                "shorthand": r.shorthand,
            }
            for r in sorted(by_chat.values(), key=lambda r: r.chat_id)
        ]
        duplicate_payload.append(
            {
                "club_id": club_id,
                "club_name": club_name,
                "gg_player_id": gg_player_id,
                "chat_count": len(chats),
                "chats": chats,
            }
        )

    summary = ScanSummary(
        mtproto_club_key=club_key,
        club_display_name=cfg.club_display_name,
        dialogs_scanned=dialogs_scanned,
        group_dialogs=group_dialogs,
        parseable=parseable,
        unparseable_title=unparseable_title,
        unknown_shorthand=unknown_shorthand,
        duplicate_sets=len(duplicate_payload),
    )
    return summary, duplicate_payload


def _print_human(summary: ScanSummary, duplicates: list[dict[str, Any]]) -> None:
    s = summary
    print(f"MTProto club: {s.mtproto_club_key} ({s.club_display_name})")
    print(
        f"Dialogs scanned: {s.dialogs_scanned} | "
        f"group/supergroup: {s.group_dialogs} | "
        f"parseable: {s.parseable} | "
        f"skipped (title): {s.unparseable_title} | "
        f"skipped (unknown shorthand): {s.unknown_shorthand}"
    )
    print(f"Duplicate sets (same club + GG player ID, 2+ chats): {s.duplicate_sets}")
    print()

    if not duplicates:
        print("No duplicates found.")
        return

    for i, dup in enumerate(duplicates, start=1):
        club_label = dup.get("club_name") or f"club_id={dup['club_id']}"
        print(f"--- Duplicate {i}: {club_label} | GG player {dup['gg_player_id']} ---")
        for chat in dup["chats"]:
            print(f"  chat_id={chat['chat_id']}")
            print(f"    title: {chat['title']}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "List group chats where the same GG player ID appears on multiple "
            "groups within one club (Telethon title scan)."
        )
    )
    parser.add_argument(
        "--club-key",
        required=True,
        choices=CLUB_KEYS,
        help="Club MTProto session to use (round_table, creator_club, clubgto).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON report to stdout instead of human-readable text.",
    )
    args = parser.parse_args()

    try:
        summary, duplicates = asyncio.run(_scan(args.club_key))
    except Exception as e:
        msg = str(e)
        if "DATABASE_URL" in msg:
            print(
                "DATABASE_URL is required to resolve club shorthand to clubs.id.",
                file=sys.stderr,
            )
        elif "AuthKeyDuplicated" in type(e).__name__ or "AuthKeyDuplicated" in msg:
            print(
                "ERROR: The MTProto session is already in use by the bot worker.\n"
                "Stop the bot worker first, or set GC_MTPROTO_ENABLED=false on Heroku and restart.",
                file=sys.stderr,
            )
        raise SystemExit(msg) from e

    if args.json:
        out = {
            "summary": asdict(summary),
            "duplicates": duplicates,
        }
        print(json.dumps(out, indent=2))
    else:
        _print_human(summary, duplicates)

    sys.exit(1 if summary.duplicate_sets else 0)


if __name__ == "__main__":
    main()
