"""Set club photos on RT/CC support chats that currently have none.

Prefer the hourly worker job in ``bot/services/group_photo_backfill.py``
(active idle-episode RT/CC groups, 10/hour, live listener — no worker pause).
This script is a one-off dedicated-session tool.

Checks live Telegram (empty vs already set), not just
``support_group_chats.group_photo_path``. Groups that already have a photo
are left alone. Dry-run by default; pass ``--apply`` to upload.

Operational: do not run while the Heroku worker holds the same club Telethon
session. Set ``GC_MTPROTO_ENABLED=false`` (or ``GC_DM_GC_LISTENER_ENABLED=false``)
on the worker and restart before running; re-enable after.

Environment: DATABASE_URL, TG_API_ID, TG_API_HASH (same as other MTProto scripts).

Usage:
  python scripts/backfill_support_group_photos.py
  python scripts/backfill_support_group_photos.py --db-only
  python scripts/backfill_support_group_photos.py --club-key creator_club --limit 1
  python scripts/backfill_support_group_photos.py --chat-id -1001234567890
  python scripts/backfill_support_group_photos.py --chat-id -1001234567890 --apply
  python scripts/backfill_support_group_photos.py --apply --club-key round_table --delay 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from telethon.errors import FloodWaitError
from telethon.tl.types import Channel, Chat

from bot.services.group_photo_backfill import classify_group_photo_action

logger = logging.getLogger("backfill_support_group_photos")

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

CLUB_KEYS = ("round_table", "creator_club", "clubgto")
DEFAULT_CLUB_KEYS = ("round_table", "creator_club")


@dataclass(frozen=True)
class PhotoRow:
    row_id: int
    club_key: str
    title: str
    telegram_chat_id: int
    stored_photo_path: str | None
    photo_path: str | None
    status: str


@dataclass(frozen=True)
class PhotoSummary:
    apply_mode: bool
    db_only: bool
    clubs_scanned: int
    groups_considered: int
    already_has_photo: int
    would_apply: int
    applied: int
    admin_not_in_group: int
    missing_photo_file: int
    skipped_limit: int
    errors: int


def _configure_logging(*, quiet: bool) -> None:
    level = logging.WARNING if quiet else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
        force=True,
    )


def _gc_label(title: str, chat_id: int) -> str:
    name = (title or "").strip()
    return name if name else f"chat {chat_id}"


def _is_group_dialog(dialog) -> bool:
    if dialog.is_group:
        return True
    if dialog.is_channel:
        entity = dialog.entity
        return bool(getattr(entity, "megagroup", False))
    return False


def _lookup_entity(entity_map: dict[int, Any], chat_id: int) -> Any | None:
    from notification.chat_id import telegram_chat_id_variants

    for cid in telegram_chat_id_variants(int(chat_id)):
        if cid in entity_map:
            return entity_map[cid]
    return None


def _load_rows(club_keys: tuple[str, ...], chat_id: int | None) -> list[Any]:
    from db.connection import get_db
    from db.models import SupportGroupChat
    from notification.chat_id import telegram_chat_id_variants

    with get_db() as session:
        q = session.query(SupportGroupChat).filter(
            SupportGroupChat.club_key.in_(club_keys)
        )
        if chat_id is not None:
            q = q.filter(
                SupportGroupChat.telegram_chat_id.in_(
                    telegram_chat_id_variants(int(chat_id))
                )
            )
        rows = (
            q.order_by(SupportGroupChat.club_key.asc(), SupportGroupChat.id.desc())
            .all()
        )
        for row in rows:
            session.expunge(row)

    seen: set[tuple[str, int]] = set()
    out: list[Any] = []
    for row in rows:
        key = (row.club_key, min(telegram_chat_id_variants(int(row.telegram_chat_id))))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


async def _sleep_flood(tag: str, seconds: int) -> None:
    wait = float(seconds) + 1.0
    logger.warning("%s FloodWait %ss — sleeping", tag, seconds)
    await asyncio.sleep(wait)


async def _resolve_one_entity(client, chat_id: int):
    from notification.chat_id import telegram_chat_id_variants

    last_exc: Exception | None = None
    for cid in telegram_chat_id_variants(int(chat_id)):
        try:
            entity = await client.get_entity(int(cid))
            if isinstance(entity, (Channel, Chat)):
                return entity
        except FloodWaitError as e:
            await _sleep_flood(f"get_entity:{cid}", e.seconds)
            try:
                entity = await client.get_entity(int(cid))
                if isinstance(entity, (Channel, Chat)):
                    return entity
            except Exception as exc:
                last_exc = exc
        except Exception as exc:
            last_exc = exc
    if last_exc is not None:
        logger.info(
            "Could not resolve chat_id=%s (%s)",
            chat_id,
            type(last_exc).__name__,
        )
    return None


async def _dialog_entity_map(client) -> dict[int, Any]:
    out: dict[int, Any] = {}
    while True:
        try:
            async for dialog in client.iter_dialogs():
                if not _is_group_dialog(dialog):
                    continue
                entity = dialog.entity
                if entity is None:
                    continue
                out[int(dialog.id)] = entity
            return out
        except FloodWaitError as e:
            await _sleep_flood("iter_dialogs", e.seconds)


async def _apply_photo_with_flood(client, entity, photo_abs: Path) -> None:
    from bot.services.mtproto_group_create import _apply_group_photo_entity

    while True:
        try:
            await _apply_group_photo_entity(client, entity, photo_abs)
            return
        except FloodWaitError as e:
            await _sleep_flood("EditGroupPhoto", e.seconds)


def _photo_abs_for_cfg(cfg) -> Path | None:
    from bot.services.mtproto_group_create import resolve_repo_path

    if not cfg.group_photo_path:
        return None
    photo_abs = resolve_repo_path(cfg.group_photo_path)
    if not photo_abs.exists():
        return None
    return photo_abs


async def _backfill(
    *,
    club_keys: tuple[str, ...],
    chat_id: int | None,
    apply: bool,
    db_only: bool,
    limit: int | None,
    delay_seconds: float,
) -> tuple[PhotoSummary, list[dict[str, Any]]]:
    from club_gc_settings import CLUB_GC_CONFIG
    from bot.services.mtproto_group_create import (
        entity_has_group_photo,
        get_mtproto_lock,
        is_client_authorized,
        make_client,
    )
    from bot.services.support_group_chats import update_support_group_chat_row

    rows = _load_rows(club_keys, chat_id)
    results: list[PhotoRow] = []
    already_has_photo = 0
    would_apply = 0
    applied = 0
    admin_not_in_group = 0
    missing_photo_file = 0
    skipped_limit = 0
    errors = 0
    clubs_scanned = 0
    apply_budget = 0

    if db_only:
        for row in rows:
            stored = row.group_photo_path
            status = "db_has_path" if stored else "db_missing_path"
            results.append(
                PhotoRow(
                    row_id=int(row.id),
                    club_key=row.club_key,
                    title=row.telegram_chat_title or "",
                    telegram_chat_id=int(row.telegram_chat_id),
                    stored_photo_path=stored,
                    photo_path=None,
                    status=status,
                )
            )
        return (
            PhotoSummary(
                apply_mode=False,
                db_only=True,
                clubs_scanned=len({r.club_key for r in rows}),
                groups_considered=len(rows),
                already_has_photo=sum(1 for r in results if r.status == "db_has_path"),
                would_apply=sum(1 for r in results if r.status == "db_missing_path"),
                applied=0,
                admin_not_in_group=0,
                missing_photo_file=0,
                skipped_limit=0,
                errors=0,
            ),
            [asdict(r) for r in results],
        )

    by_club: dict[str, list[Any]] = {}
    for row in rows:
        by_club.setdefault(row.club_key, []).append(row)

    for club_key in club_keys:
        club_rows = by_club.get(club_key) or []
        if not club_rows:
            continue
        cfg = CLUB_GC_CONFIG.get(club_key)
        if cfg is None:
            logger.warning("No MTProto config for club_key=%s — skipped", club_key)
            errors += len(club_rows)
            for row in club_rows:
                results.append(
                    PhotoRow(
                        row_id=int(row.id),
                        club_key=club_key,
                        title=row.telegram_chat_title or "",
                        telegram_chat_id=int(row.telegram_chat_id),
                        stored_photo_path=row.group_photo_path,
                        photo_path=None,
                        status="no_mtproto_config",
                    )
                )
            continue

        clubs_scanned += 1
        photo_abs = _photo_abs_for_cfg(cfg)
        if photo_abs is None:
            missing_photo_file += len(club_rows)
            for row in club_rows:
                results.append(
                    PhotoRow(
                        row_id=int(row.id),
                        club_key=club_key,
                        title=row.telegram_chat_title or "",
                        telegram_chat_id=int(row.telegram_chat_id),
                        stored_photo_path=row.group_photo_path,
                        photo_path=cfg.group_photo_path,
                        status="missing_photo_file",
                    )
                )
            continue

        if not await is_client_authorized(cfg):
            raise RuntimeError(
                f"Telethon session not authorized for club_key={club_key!r}"
            )

        async with get_mtproto_lock(club_key):
            client = make_client(cfg)
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    raise RuntimeError(
                        f"Telethon not authorized after connect (club_key={club_key})"
                    )

                entity_map: dict[int, Any] | None = None
                if chat_id is None:
                    logger.info("Loading dialogs for club_key=%s", club_key)
                    entity_map = await _dialog_entity_map(client)

                for row in club_rows:
                    title = row.telegram_chat_title or ""
                    cid = int(row.telegram_chat_id)
                    if entity_map is not None:
                        entity = _lookup_entity(entity_map, cid)
                    else:
                        entity = await _resolve_one_entity(client, cid)

                    action = classify_group_photo_action(
                        entity_found=entity is not None,
                        has_photo=entity_has_group_photo(entity)
                        if entity is not None
                        else False,
                    )
                    if action == "admin_not_in_group":
                        admin_not_in_group += 1
                        status = action
                    elif action == "already_has_photo":
                        already_has_photo += 1
                        status = action
                    elif limit is not None and apply_budget >= limit:
                        skipped_limit += 1
                        status = "skipped_limit"
                    elif apply:
                        try:
                            await _apply_photo_with_flood(client, entity, photo_abs)
                            ok, err = update_support_group_chat_row(
                                int(row.id),
                                group_photo_path=cfg.group_photo_path,
                            )
                            if not ok:
                                logger.warning(
                                    "Photo set but DB update failed row_id=%s: %s",
                                    row.id,
                                    err,
                                )
                            applied += 1
                            apply_budget += 1
                            status = "applied"
                            if delay_seconds > 0:
                                await asyncio.sleep(delay_seconds)
                        except Exception as e:
                            errors += 1
                            status = f"error:{type(e).__name__}"
                            logger.warning(
                                "Photo apply failed %s chat_id=%s: %s",
                                _gc_label(title, cid),
                                cid,
                                type(e).__name__,
                            )
                    else:
                        would_apply += 1
                        apply_budget += 1
                        status = "would_apply"

                    logger.info(
                        "%s club=%s chat_id=%s status=%s",
                        _gc_label(title, cid),
                        club_key,
                        cid,
                        status,
                    )
                    results.append(
                        PhotoRow(
                            row_id=int(row.id),
                            club_key=club_key,
                            title=title,
                            telegram_chat_id=cid,
                            stored_photo_path=row.group_photo_path,
                            photo_path=cfg.group_photo_path,
                            status=status,
                        )
                    )
            finally:
                await client.disconnect()

    summary = PhotoSummary(
        apply_mode=apply,
        db_only=False,
        clubs_scanned=clubs_scanned,
        groups_considered=len(rows),
        already_has_photo=already_has_photo,
        would_apply=would_apply,
        applied=applied,
        admin_not_in_group=admin_not_in_group,
        missing_photo_file=missing_photo_file,
        skipped_limit=skipped_limit,
        errors=errors,
    )
    return summary, [asdict(r) for r in results]


def _print_human(summary: PhotoSummary, rows: list[dict[str, Any]]) -> None:
    if summary.db_only:
        mode = "DB-ONLY (Telegram not checked)"
    elif summary.apply_mode:
        mode = "APPLY"
    else:
        mode = "DRY-RUN"
    print(f"Support group photo backfill ({mode})")
    print(
        f"Groups considered: {summary.groups_considered} | clubs scanned: {summary.clubs_scanned}"
    )
    if summary.db_only:
        print(
            f"DB has path: {summary.already_has_photo} | DB missing path: {summary.would_apply}"
        )
        print("DB path is not live Telegram state — re-run without --db-only to check photos.")
    else:
        print(
            f"Already has photo: {summary.already_has_photo} | "
            f"would apply: {summary.would_apply} | applied: {summary.applied}"
        )
        print(
            f"Admin not in group: {summary.admin_not_in_group} | "
            f"missing photo file: {summary.missing_photo_file} | "
            f"skipped limit: {summary.skipped_limit} | errors: {summary.errors}"
        )
    print()

    actionable = [
        r
        for r in rows
        if r.get("status") in ("would_apply", "applied", "db_missing_path")
    ]
    if actionable:
        print(f"--- Needs photo ({len(actionable)}) ---")
        for r in actionable[:50]:
            print(
                f"  club={r['club_key']} chat_id={r['telegram_chat_id']} "
                f"row_id={r['row_id']} status={r['status']}"
            )
            print(f"    title: {r['title']}")
        if len(actionable) > 50:
            print(f"  ... and {len(actionable) - 50} more")
        print()

    if not summary.apply_mode and not summary.db_only and summary.would_apply:
        print("Dry-run only. Re-run with --apply to upload photos.")
        print("Pause the worker MTProto session first (GC_MTPROTO_ENABLED=false).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--club-key",
        choices=CLUB_KEYS,
        help="Limit to one club (default: round_table + creator_club).",
    )
    parser.add_argument(
        "--chat-id",
        type=int,
        help="Limit to one support_group_chats telegram chat id.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max groups to apply / would-apply (skips already-has-photo from the count).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Upload photos to Telegram (default: report only).",
    )
    parser.add_argument(
        "--db-only",
        action="store_true",
        help="List support_group_chats paths without connecting Telegram.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="Pause between uploads when --apply (default: 2).",
    )
    parser.add_argument("--json", action="store_true", help="JSON to stdout.")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only warnings/errors on stderr.",
    )
    args = parser.parse_args()

    if args.apply and args.db_only:
        raise SystemExit("Cannot combine --apply with --db-only.")
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be >= 1.")

    club_keys = (args.club_key,) if args.club_key else DEFAULT_CLUB_KEYS

    if not args.json:
        _configure_logging(quiet=args.quiet)
        if not args.db_only:
            logger.warning(
                "Do not run while the worker holds the same Telethon session. "
                "Set GC_MTPROTO_ENABLED=false and restart worker first."
            )

    summary, rows = asyncio.run(
        _backfill(
            club_keys=club_keys,
            chat_id=args.chat_id,
            apply=bool(args.apply),
            db_only=bool(args.db_only),
            limit=args.limit,
            delay_seconds=max(0.0, float(args.delay)),
        )
    )

    if args.json:
        print(json.dumps({"summary": asdict(summary), "groups": rows}, indent=2))
    else:
        _print_human(summary, rows)

    if summary.errors and args.apply:
        sys.exit(2)


if __name__ == "__main__":
    main()
