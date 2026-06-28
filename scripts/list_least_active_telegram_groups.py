"""List the least active Telegram support megagroups for a club MTProto account.

Scans Telethon dialogs and sorts by last **non-support** message date (oldest
first). By default only **megagroups** (``-100…`` supergroups) are included;
legacy basic groups are skipped unless ``--include-basic-groups`` is passed.
Messages from the club MTProto account, ``GC_USERS_TO_INVITE`` staff, and
the configured ``/gc`` bot are ignored so cleanup targets groups with no recent
player or third-party activity.

Environment: TG_API_ID, TG_API_HASH, and Postgres-backed MTProto sessions (same as
other ``scripts/*`` Telethon tools).

Operational: do not run while the Heroku worker holds the same club Telethon
session. Set ``GC_MTPROTO_ENABLED=false`` (or ``GC_DM_GC_LISTENER_ENABLED=false``)
on the worker and restart before running locally.

Usage:
  python scripts/list_least_active_telegram_groups.py
  python scripts/list_least_active_telegram_groups.py --club-key round_table
  python scripts/list_least_active_telegram_groups.py --limit 400 --output backups/rts2_cleanup.csv
  python scripts/list_least_active_telegram_groups.py --include-basic-groups
  python scripts/list_least_active_telegram_groups.py --history-limit 200
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("list_least_active_telegram_groups")

if sys.version_info < (3, 10):
    raise SystemExit(
        f"Python 3.10+ required (this interpreter is {sys.version.split()[0]}). "
        "Use pyenv/python3.11 or recreate the venv: "
        "python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    )

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

CLUB_KEYS = ("round_table", "creator_club", "clubgto")

CSV_COLUMNS = (
    "rank",
    "title",
    "chat_id",
    "kind",
    "last_external_message_date",
    "last_external_message_time_utc",
    "inactive_days",
    "inactive_label",
    "activity_basis",
    "duplicate_title",
    "newer_same_title_chat_id",
    "archived",
    "unread_count",
    "account_username",
    "account_user_id",
)


@dataclass(frozen=True)
class DialogActivityRow:
    title: str
    chat_id: int
    kind: str
    last_message_at: datetime | None
    activity_basis: str
    archived: bool
    unread_count: int
    duplicate_title: bool = False
    newer_same_title_chat_id: int | None = None


def _dialog_kind(dialog) -> str | None:
    """Return dialog kind, or None when the dialog should be skipped."""

    from telethon.tl.types import Channel, Chat, User

    entity = dialog.entity
    if isinstance(entity, User):
        return None
    if isinstance(entity, Chat):
        return "basic_group"
    if isinstance(entity, Channel):
        if entity.megagroup:
            return "megagroup"
        if entity.broadcast:
            return "channel"
        return "group"
    return None


def _utc_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _inactive_days(last_message_at: datetime | None, *, now: datetime) -> int | None:
    if last_message_at is None:
        return None
    delta = now - last_message_at
    return max(0, delta.days)


def _inactive_label(days: int | None) -> str:
    if days is None:
        return "unknown"
    if days == 0:
        return "today"
    if days == 1:
        return "1 day"
    if days < 30:
        return f"{days} days"
    if days < 365:
        months = max(1, days // 30)
        return "1 month" if months == 1 else f"{months} months"
    years = max(1, days // 365)
    return "1 year" if years == 1 else f"{years} years"


def _default_output_path(club_key: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _REPO_ROOT / "backups" / f"least_active_groups_{club_key}_{ts}.csv"


def _format_csv_row(
    rank: int,
    row: DialogActivityRow,
    *,
    account_username: str | None,
    account_user_id: int,
    now: datetime,
) -> dict[str, str | int]:
    last = _utc_dt(row.last_message_at)
    inactive_days = _inactive_days(last, now=now)
    return {
        "rank": rank,
        "title": row.title,
        "chat_id": row.chat_id,
        "kind": row.kind,
        "last_external_message_date": last.strftime("%Y-%m-%d") if last else "",
        "last_external_message_time_utc": last.strftime("%H:%M:%S") if last else "",
        "inactive_days": "" if inactive_days is None else inactive_days,
        "inactive_label": _inactive_label(inactive_days),
        "activity_basis": row.activity_basis,
        "duplicate_title": "yes" if row.duplicate_title else "no",
        "newer_same_title_chat_id": row.newer_same_title_chat_id or "",
        "archived": "yes" if row.archived else "no",
        "unread_count": row.unread_count,
        "account_username": account_username or "",
        "account_user_id": account_user_id,
    }


def _message_sender_id(message: Any) -> int | None:
    sender_id = getattr(message, "sender_id", None)
    if sender_id is not None:
        return int(sender_id)
    from_id = getattr(message, "from_id", None)
    if from_id is None:
        return None
    from telethon.utils import get_peer_id

    try:
        return int(get_peer_id(from_id))
    except Exception:
        return None


async def _resolve_exclude_user_ids(client, cfg, me_id: int) -> frozenset[int]:
    """User ids whose messages should not count as external activity."""

    from club_gc_settings import get_gc_users_to_add

    exclude: set[int] = {int(me_id)}
    markers = list(get_gc_users_to_add(cfg))
    bot_account = (cfg.bot_account or "").strip()
    if bot_account:
        markers.append(bot_account)

    for marker in markers:
        lookup = marker.strip()
        if not lookup:
            continue
        if not lookup.startswith("@") and not lookup.lstrip("-").isdigit():
            lookup = f"@{lookup.lstrip('@')}"
        try:
            ent = await client.get_entity(lookup)
            exclude.add(int(ent.id))
        except Exception as exc:
            logger.warning("Could not resolve exclude marker %s: %s", marker, type(exc).__name__)

    return frozenset(exclude)


async def _last_external_message_at(
    client,
    entity,
    *,
    exclude_user_ids: frozenset[int],
    history_limit: int,
) -> tuple[datetime | None, str]:
    """Return last message time from a non-excluded sender, plus activity basis."""

    latest = await client.get_messages(entity, limit=1)
    if not latest:
        return None, "empty"

    msg = latest[0]
    sender_id = _message_sender_id(msg)
    if sender_id is not None and sender_id not in exclude_user_ids:
        return _utc_dt(msg.date), "external"

    async for msg in client.iter_messages(entity, limit=history_limit):
        sender_id = _message_sender_id(msg)
        if sender_id is None:
            continue
        if sender_id not in exclude_user_ids:
            return _utc_dt(msg.date), "external"

    return None, "support_only"


def _annotate_duplicate_titles(rows: list[DialogActivityRow]) -> list[DialogActivityRow]:
    """Flag stale dialogs that share a title with a newer chat (common after supergroup migration)."""

    by_title: dict[str, list[DialogActivityRow]] = {}
    for row in rows:
        key = row.title.casefold()
        by_title.setdefault(key, []).append(row)

    out: list[DialogActivityRow] = []
    for row in rows:
        peers = by_title.get(row.title.casefold(), [row])
        if len(peers) < 2:
            out.append(row)
            continue

        newer = max(
            (peer for peer in peers if peer.chat_id != row.chat_id),
            key=lambda peer: peer.last_message_at
            or datetime.min.replace(tzinfo=timezone.utc),
            default=None,
        )
        if newer is None:
            out.append(row)
            continue

        row_last = row.last_message_at or datetime.min.replace(tzinfo=timezone.utc)
        newer_last = newer.last_message_at or datetime.min.replace(tzinfo=timezone.utc)
        if newer_last <= row_last:
            out.append(row)
            continue

        out.append(
            DialogActivityRow(
                title=row.title,
                chat_id=row.chat_id,
                kind=row.kind,
                last_message_at=row.last_message_at,
                activity_basis=row.activity_basis,
                archived=row.archived,
                unread_count=row.unread_count,
                duplicate_title=True,
                newer_same_title_chat_id=newer.chat_id,
            )
        )
    return out


async def _scan_dialogs(
    club_key: str,
    *,
    include_basic_groups: bool,
    groups_only: bool,
    history_limit: int,
) -> tuple[str | None, int, list[DialogActivityRow]]:
    from club_gc_settings import CLUB_GC_CONFIG
    from bot.services.mtproto_group_create import is_client_authorized, make_client
    from telethon.utils import get_peer_id

    cfg = CLUB_GC_CONFIG.get(club_key)
    if cfg is None:
        raise SystemExit(f"Unknown club_key: {club_key!r} (expected one of {CLUB_KEYS})")

    if not await is_client_authorized(cfg):
        raise SystemExit(
            f"MTProto session not authorized for {club_key!r}. "
            "Complete Dashboard → Telegram login first."
        )

    client = make_client(cfg)
    await client.connect()
    rows: list[DialogActivityRow] = []
    try:
        if not await client.is_user_authorized():
            raise SystemExit(f"MTProto session not authorized for {club_key!r}.")

        me = await client.get_me()
        account_username = me.username.strip() if me.username else None
        account_user_id = int(me.id)
        exclude_user_ids = await _resolve_exclude_user_ids(client, cfg, account_user_id)
        logger.info(
            "Excluding messages from %d support/bot user ids (includes MTProto account).",
            len(exclude_user_ids),
        )

        scanned = 0
        async for dialog in client.iter_dialogs():
            kind = _dialog_kind(dialog)
            if kind is None:
                continue
            if groups_only and kind == "channel":
                continue
            if kind != "megagroup" and not (include_basic_groups and kind == "basic_group"):
                continue

            last_external, basis = await _last_external_message_at(
                client,
                dialog.entity,
                exclude_user_ids=exclude_user_ids,
                history_limit=history_limit,
            )
            rows.append(
                DialogActivityRow(
                    title=(dialog.title or "").strip() or "(untitled)",
                    chat_id=int(get_peer_id(dialog.entity)),
                    kind=kind,
                    last_message_at=last_external,
                    activity_basis=basis,
                    archived=bool(getattr(dialog, "archived", False)),
                    unread_count=int(getattr(dialog, "unread_count", 0) or 0),
                )
            )
            scanned += 1
            if scanned % 50 == 0:
                logger.info("Scanned %d group/channel dialogs…", scanned)
    finally:
        await client.disconnect()

    rows = _annotate_duplicate_titles(rows)
    rows.sort(
        key=lambda row: (
            row.last_message_at is None,
            row.last_message_at or datetime.max.replace(tzinfo=timezone.utc),
            row.title.lower(),
        )
    )
    return account_username, account_user_id, rows


def _write_csv(
    path: Path,
    *,
    account_username: str | None,
    account_user_id: int,
    rows: list[DialogActivityRow],
    limit: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    selected = rows[:limit]

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=CSV_COLUMNS,
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\n",
        )
        writer.writeheader()
        for rank, row in enumerate(selected, start=1):
            writer.writerow(
                _format_csv_row(
                    rank,
                    row,
                    account_username=account_username,
                    account_user_id=account_user_id,
                    now=now,
                )
            )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export the least active Telegram support megagroups for a club MTProto account."
    )
    parser.add_argument(
        "--club-key",
        choices=CLUB_KEYS,
        default="round_table",
        help="Club MTProto session to scan (default: round_table).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=400,
        help="Number of least-active dialogs to write (default: 400).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV path (default: backups/least_active_groups_<club>_<ts>.csv).",
    )
    parser.add_argument(
        "--include-basic-groups",
        action="store_true",
        help="Also scan legacy basic groups (default: megagroups only).",
    )
    parser.add_argument(
        "--groups-only",
        action="store_true",
        help="With --include-basic-groups, exclude broadcast channels.",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=100,
        help="Max recent messages to scan per chat when latest is from support (default: 100).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log scan progress (recommended; full scan can take several minutes).",
    )
    return parser.parse_args()


def _configure_logging(verbose: bool) -> None:
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")


def main() -> int:
    args = _parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be >= 1")
    if args.history_limit < 1:
        raise SystemExit("--history-limit must be >= 1")

    _configure_logging(args.verbose)

    output = args.output or _default_output_path(args.club_key)
    account_username, account_user_id, rows = asyncio.run(
        _scan_dialogs(
            args.club_key,
            include_basic_groups=args.include_basic_groups,
            groups_only=args.groups_only,
            history_limit=args.history_limit,
        )
    )

    if not rows:
        print("No megagroup dialogs found.", file=sys.stderr)
        return 1

    _write_csv(
        output,
        account_username=account_username,
        account_user_id=account_user_id,
        rows=rows,
        limit=args.limit,
    )

    selected = min(args.limit, len(rows))
    account_label = f"@{account_username}" if account_username else str(account_user_id)
    print(f"Account: {account_label} ({account_user_id})")
    scope = "megagroups + basic groups" if args.include_basic_groups else "megagroups"
    print(f"Scanned: {len(rows)} {scope}")
    print(f"Wrote: {selected} rows → {output}")

    preview = rows[:5]
    if preview:
        print("\nOldest 5:")
        now = datetime.now(timezone.utc)
        for idx, row in enumerate(preview, start=1):
            last = _utc_dt(row.last_message_at)
            label = _inactive_label(_inactive_days(last, now=now))
            print(f"  {idx}. [{label}] {row.title} ({row.kind})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
