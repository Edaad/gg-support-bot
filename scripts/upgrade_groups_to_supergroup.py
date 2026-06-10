"""Upgrade legacy Telegram basic groups to supergroups via Telethon MigrateChat.

Candidates are unioned from ``groups`` and ``player_details.chat_ids``, scoped to clubs
with MTProto ``/gc`` config. Only chats where the club MTProto user is still a member
(and can classify the chat) are considered. Dry-run by default; pass --apply to call
``messages.MigrateChatRequest`` and remap chat ids in Postgres.

Environment: DATABASE_URL, TG_API_ID, TG_API_HASH (same as other MTProto scripts).

Operational: Do not run while the Heroku bot worker holds the same club Telethon
session. Set ``GC_MTPROTO_ENABLED=false`` on the worker (or ``GC_DM_GC_LISTENER_ENABLED=false``)
and restart before upgrading; re-enable after the run. Re-check that the support bot
remains in each group after migration.

Usage:
  python scripts/upgrade_groups_to_supergroup.py
  python scripts/upgrade_groups_to_supergroup.py --club-key clubgto
  python scripts/upgrade_groups_to_supergroup.py --chat-id -5287778428
  python scripts/upgrade_groups_to_supergroup.py --apply
  python scripts/upgrade_groups_to_supergroup.py --apply --json

``--apply`` runs ``pg_dump`` first (custom format under ``backups/``) unless
``--skip-backup``. Requires ``pg_dump`` on PATH (PostgreSQL client tools).

Telegram only allows migration when the MTProto account is the group creator (or has
permission). Groups already supergroups on Telegram but still stored with a legacy id
in Postgres are remapped without calling migrate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("upgrade_groups_to_supergroup")

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
    LinkedGroupRow,
    _configure_logging,
    _find_dialog_for_group,
    _gc_display_name,
    _list_admin_group_dialogs,
    _load_tracked_groups,
    _refresh_db_pool,
)


@dataclass(frozen=True)
class UpgradeRow:
    stored_chat_id: int
    club_id: int
    club_key: str | None
    club_display_name: str | None
    title: str
    dialog_title: str | None
    dialog_chat_id: int | None
    telegram_kind: str | None
    new_chat_id: int | None
    status: str
    db_rows_updated: int = 0


def _progress(show: bool, msg: str) -> None:
    logger.info("%s", msg)
    if show:
        print(msg, flush=True)


def _gc_label(title: str, chat_id: int) -> str:
    return _gc_display_name(title, chat_id)


def _format_gc_context(
    *,
    club_display_name: str | None,
    club_key: str | None,
    title: str,
    stored_chat_id: int,
    dialog_title: str | None = None,
    dialog_chat_id: int | None = None,
    telegram_kind: str | None = None,
    new_chat_id: int | None = None,
    status: str | None = None,
) -> str:
    club = club_display_name or club_key or "?"
    name = _gc_label(title or (dialog_title or ""), stored_chat_id)
    parts = [
        f"GC {name!r}",
        f"club={club}",
    ]
    if club_key:
        parts.append(f"club_key={club_key}")
    parts.append(f"stored_chat_id={stored_chat_id}")
    if dialog_chat_id is not None:
        parts.append(f"dialog_chat_id={dialog_chat_id}")
    if dialog_title and dialog_title.strip() and dialog_title.strip() != (title or "").strip():
        parts.append(f"dialog_title={dialog_title!r}")
    if telegram_kind:
        parts.append(f"telegram={telegram_kind}")
    if new_chat_id is not None:
        parts.append(f"new_chat_id={new_chat_id}")
    if status:
        parts.append(f"status={status}")
    return " | ".join(parts)


@dataclass(frozen=True)
class UpgradeSummary:
    apply_mode: bool
    clubs_scanned: int
    groups_considered: int
    already_supergroup: int
    db_remapped_only: int
    migrated: int
    would_migrate: int
    admin_not_in_group: int
    no_mtproto_config: int
    migrate_failed: int
    db_update_failed: int
    errors: int


def _is_legacy_basic_chat_id(chat_id: int) -> bool:
    """True for Bot API basic-group ids (negative, not ``-100…``)."""
    cid = int(chat_id)
    if cid >= 0:
        return False
    return not str(cid).startswith("-100")


def _is_stored_supergroup_form(chat_id: int) -> bool:
    """True when Postgres already stores a Bot API supergroup id (``-100…``)."""
    cid = int(chat_id)
    return cid < 0 and str(cid).startswith("-100")


def _partition_tracked_groups(
    club_groups: list[LinkedGroupRow],
    *,
    include_supergroup_ids: bool,
) -> tuple[list[LinkedGroupRow], list[LinkedGroupRow]]:
    """Split into candidates vs stored ``-100…`` ids (skipped unless --include-supergroup-ids)."""
    if include_supergroup_ids:
        return club_groups, []
    candidates: list[LinkedGroupRow] = []
    skipped: list[LinkedGroupRow] = []
    for g in club_groups:
        if _is_stored_supergroup_form(g.chat_id):
            skipped.append(g)
        else:
            candidates.append(g)
    return candidates, skipped


def _extract_migrated_chat_id(updates) -> int | None:
    from telethon.tl.types import Channel, MessageActionChatMigrateTo, MessageService
    from telethon.utils import get_peer_id

    for chat in getattr(updates, "chats", []) or []:
        if isinstance(chat, Channel) and bool(getattr(chat, "megagroup", False)):
            return int(get_peer_id(chat))

    for update in getattr(updates, "updates", []) or []:
        msg = getattr(update, "message", None)
        if not isinstance(msg, MessageService):
            continue
        action = getattr(msg, "action", None)
        if isinstance(action, MessageActionChatMigrateTo):
            from telethon.tl.types import PeerChannel

            return int(get_peer_id(PeerChannel(int(action.channel_id))))

    return None


async def _classify_group_entity(client, chat_id: int) -> tuple[str, int | None]:
    """Return (kind, bot_api_chat_id).

    kind: basic | supergroup | channel | entity_error:<name>
    """
    from telethon.tl.types import Channel, Chat
    from telethon.utils import get_peer_id

    try:
        entity = await client.get_entity(int(chat_id))
    except Exception as e:
        return f"entity_error:{type(e).__name__}", None

    if isinstance(entity, Chat):
        return "basic", int(get_peer_id(entity))
    if isinstance(entity, Channel):
        peer_id = int(get_peer_id(entity))
        if bool(getattr(entity, "megagroup", False)):
            return "supergroup", peer_id
        return "channel", peer_id
    return "unknown", None


async def _migrate_basic_group(client, chat_id: int) -> int:
    from telethon.tl import functions
    from telethon.tl.types import Chat

    entity = await client.get_entity(int(chat_id))
    if not isinstance(entity, Chat):
        raise RuntimeError("chat is not a basic group")
    updates = await client(
        functions.messages.MigrateChatRequest(chat_id=int(entity.id))
    )
    new_id = _extract_migrated_chat_id(updates)
    if new_id is None:
        raise RuntimeError("migrate succeeded but new chat id not found in updates")
    return int(new_id)


def _remap_chat_id_in_db(old_id: int, new_id: int) -> dict[str, int]:
    from bot.services.chat_id_remap import remap_chat_id_in_db

    return remap_chat_id_in_db(old_id, new_id)


def _error_label(exc: BaseException) -> str:
    msg = str(exc).strip().replace("\n", " ")
    if len(msg) > 160:
        msg = msg[:157] + "..."
    name = type(exc).__name__
    return f"{name}: {msg}" if msg else name


def _backup_database(
    *,
    backup_dir: Path | None,
    show_progress: bool,
) -> Path:
    """Full Postgres dump via pg_dump before destructive --apply."""
    from db.connection import _database_url

    if shutil.which("pg_dump") is None:
        raise SystemExit(
            "pg_dump not found on PATH. Install PostgreSQL client tools, "
            "or re-run with --skip-backup (not recommended)."
        )

    url = _database_url()
    if not url:
        raise SystemExit("DATABASE_URL is not set")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = backup_dir or (_REPO_ROOT / "backups" / f"upgrade_supergroup_{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)
    dump_path = out_dir / "database.dump"

    cmd = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-acl",
        f"--file={dump_path}",
        url,
    ]
    _progress(show_progress, f"Backing up database to {dump_path} …")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "unknown error").strip()
        raise SystemExit(f"pg_dump failed ({result.returncode}): {err}")

    size_mb = dump_path.stat().st_size / (1024 * 1024)
    _progress(
        show_progress,
        f"Database backup complete: {dump_path} ({size_mb:.1f} MB). "
        "Restore with: pg_restore --clean --if-exists --no-owner --dbname=$DATABASE_URL "
        f"{dump_path}",
    )
    return dump_path


def _remap_chat_id_with_retry(old_id: int, new_id: int, *, max_attempts: int = 3) -> dict[str, int]:
    from sqlalchemy.exc import OperationalError

    last_err: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return _remap_chat_id_in_db(old_id, new_id)
        except OperationalError as e:
            last_err = e
            if attempt + 1 >= max_attempts:
                break
            logger.warning(
                "DB connection lost on remap (attempt %s/%s), retrying…",
                attempt + 1,
                max_attempts,
            )
            _refresh_db_pool()
            time.sleep(float(attempt + 1))
    assert last_err is not None
    raise last_err


async def _upgrade(
    *,
    club_key_filter: str | None,
    chat_id_filter: int | None,
    apply: bool,
    migrate_delay_seconds: float,
    include_supergroup_ids: bool,
    show_progress: bool,
) -> tuple[UpgradeSummary, list[dict[str, Any]]]:
    from club_gc_settings import CLUB_GC_CONFIG, get_club_gc_config_by_link_club_id
    from bot.services.mtproto_group_create import get_mtproto_lock, make_client
    from db.connection import init_engine
    from notification.chat_id import telegram_chat_ids_match

    init_engine()

    club_id_filter: int | None = None
    if club_key_filter:
        cfg = CLUB_GC_CONFIG.get(club_key_filter)
        if cfg is None:
            raise SystemExit(f"Unknown club_key: {club_key_filter!r}")
        club_id_filter = int(cfg.link_club_id)

    mtproto_club_ids = frozenset(
        int(cfg.link_club_id) for cfg in CLUB_GC_CONFIG.values()
    )
    groups = _load_tracked_groups(
        club_id=club_id_filter,
        chat_id=chat_id_filter,
        mtproto_club_ids=mtproto_club_ids,
    )
    mode = "APPLY" if apply else "DRY-RUN"
    stored_supergroup_total = (
        0
        if include_supergroup_ids
        else sum(1 for g in groups if _is_stored_supergroup_form(g.chat_id))
    )
    candidate_total = len(groups) - stored_supergroup_total
    _progress(
        show_progress,
        f"Supergroup upgrade ({mode}): loaded {len(groups)} tracked group chats "
        f"({candidate_total} candidates, {stored_supergroup_total} stored -100… skipped)",
    )

    by_club: dict[int, list[LinkedGroupRow]] = {}
    for row in groups:
        by_club.setdefault(row.club_id, []).append(row)

    results: list[UpgradeRow] = []
    already_supergroup = db_remapped_only = migrated = would_migrate = 0
    admin_not_in_group = no_mtproto_config = migrate_failed = db_update_failed = 0
    errors = 0
    clubs_scanned = 0

    for club_id, club_groups in sorted(by_club.items()):
        cfg = get_club_gc_config_by_link_club_id(int(club_id))
        if cfg is None:
            for g in club_groups:
                row = UpgradeRow(
                    stored_chat_id=g.chat_id,
                    club_id=g.club_id,
                    club_key=None,
                    club_display_name=None,
                    title=g.title,
                    dialog_title=None,
                    dialog_chat_id=None,
                    telegram_kind=None,
                    new_chat_id=None,
                    status="no_mtproto_config",
                )
                _progress(
                    show_progress,
                    _format_gc_context(
                        club_display_name=None,
                        club_key=None,
                        title=g.title,
                        stored_chat_id=g.chat_id,
                        status="no_mtproto_config",
                    ),
                )
                results.append(row)
                no_mtproto_config += 1
            continue

        if club_key_filter and cfg.club_key != club_key_filter:
            continue

        clubs_scanned += 1
        candidates, stored_supergroups = _partition_tracked_groups(
            club_groups,
            include_supergroup_ids=include_supergroup_ids,
        )
        for g in stored_supergroups:
            results.append(
                UpgradeRow(
                    stored_chat_id=g.chat_id,
                    club_id=g.club_id,
                    club_key=cfg.club_key,
                    club_display_name=cfg.club_display_name,
                    title=g.title,
                    dialog_title=None,
                    dialog_chat_id=None,
                    telegram_kind="supergroup",
                    new_chat_id=g.chat_id,
                    status="already_supergroup_id",
                )
            )
            already_supergroup += 1

        club_total = len(candidates)
        if stored_supergroups:
            _progress(
                show_progress,
                f"Club {cfg.club_display_name}: skipped {len(stored_supergroups)} groups "
                f"with stored -100… chat ids",
            )
        if club_total == 0:
            _progress(
                show_progress,
                f"Club {cfg.club_display_name}: no legacy basic-group candidates to scan",
            )
            continue

        _progress(
            show_progress,
            f"Club {cfg.club_display_name} (club_key={cfg.club_key}, club_id={club_id}): "
            f"scanning {club_total} candidate groups",
        )

        try:
            _progress(
                show_progress,
                f"Listing MTProto dialogs for {cfg.club_display_name} …",
            )
            dialogs = await _list_admin_group_dialogs(cfg)
            _progress(
                show_progress,
                f"Found {len(dialogs)} group dialogs for {cfg.club_display_name}",
            )
        except Exception as e:
            err = type(e).__name__
            _progress(
                show_progress,
                f"Failed to list dialogs for {cfg.club_display_name} (club_key={cfg.club_key}): {err}",
            )
            for g in candidates:
                results.append(
                    UpgradeRow(
                        stored_chat_id=g.chat_id,
                        club_id=g.club_id,
                        club_key=cfg.club_key,
                        club_display_name=cfg.club_display_name,
                        title=g.title,
                        dialog_title=None,
                        dialog_chat_id=None,
                        telegram_kind=None,
                        new_chat_id=None,
                        status=f"dialog_scan_error:{err}",
                    )
                )
                errors += 1
            continue

        pending_migrate: list[tuple[LinkedGroupRow, int, str, str]] = []

        async with get_mtproto_lock(cfg.club_key):
            client = make_client(cfg)
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    raise RuntimeError(
                        f"Telethon not authorized (club_key={cfg.club_key})"
                    )

                for idx, g in enumerate(candidates, start=1):
                    title_out = g.title
                    _progress(
                        show_progress,
                        f"[{idx}/{club_total}] Checking "
                        + _format_gc_context(
                            club_display_name=cfg.club_display_name,
                            club_key=cfg.club_key,
                            title=title_out,
                            stored_chat_id=g.chat_id,
                        ),
                    )

                    match = _find_dialog_for_group(g.chat_id, dialogs)
                    if match is None:
                        status = "admin_not_in_group"
                        _progress(
                            show_progress,
                            f"  → skip: MTProto admin not in group | "
                            + _format_gc_context(
                                club_display_name=cfg.club_display_name,
                                club_key=cfg.club_key,
                                title=title_out,
                                stored_chat_id=g.chat_id,
                                status=status,
                            ),
                        )
                        results.append(
                            UpgradeRow(
                                stored_chat_id=g.chat_id,
                                club_id=g.club_id,
                                club_key=cfg.club_key,
                                club_display_name=cfg.club_display_name,
                                title=g.title,
                                dialog_title=None,
                                dialog_chat_id=None,
                                telegram_kind=None,
                                new_chat_id=None,
                                status=status,
                            )
                        )
                        admin_not_in_group += 1
                        continue

                    dialog_chat_id, dialog_title = match
                    title_out = g.title or dialog_title
                    kind, entity_chat_id = await _classify_group_entity(
                        client, dialog_chat_id
                    )
                    _progress(
                        show_progress,
                        f"  → classified | "
                        + _format_gc_context(
                            club_display_name=cfg.club_display_name,
                            club_key=cfg.club_key,
                            title=title_out,
                            stored_chat_id=g.chat_id,
                            dialog_title=dialog_title,
                            dialog_chat_id=dialog_chat_id,
                            telegram_kind=kind,
                        ),
                    )
                    if kind.startswith("entity_error"):
                        results.append(
                            UpgradeRow(
                                stored_chat_id=g.chat_id,
                                club_id=g.club_id,
                                club_key=cfg.club_key,
                                club_display_name=cfg.club_display_name,
                                title=title_out,
                                dialog_title=dialog_title,
                                dialog_chat_id=dialog_chat_id,
                                telegram_kind=kind,
                                new_chat_id=None,
                                status=kind,
                            )
                        )
                        errors += 1
                        continue

                    if kind == "channel":
                        status = "skipped_channel_not_group"
                        _progress(
                            show_progress,
                            f"  → skip: not a group chat | "
                            + _format_gc_context(
                                club_display_name=cfg.club_display_name,
                                club_key=cfg.club_key,
                                title=title_out,
                                stored_chat_id=g.chat_id,
                                dialog_title=dialog_title,
                                dialog_chat_id=dialog_chat_id,
                                telegram_kind=kind,
                                new_chat_id=entity_chat_id,
                                status=status,
                            ),
                        )
                        results.append(
                            UpgradeRow(
                                stored_chat_id=g.chat_id,
                                club_id=g.club_id,
                                club_key=cfg.club_key,
                                club_display_name=cfg.club_display_name,
                                title=title_out,
                                dialog_title=dialog_title,
                                dialog_chat_id=dialog_chat_id,
                                telegram_kind=kind,
                                new_chat_id=entity_chat_id,
                                status=status,
                            )
                        )
                        continue

                    if kind == "supergroup":
                        new_id = int(entity_chat_id or dialog_chat_id)
                        if telegram_chat_ids_match(g.chat_id, new_id):
                            status = "already_supergroup"
                            _progress(
                                show_progress,
                                f"  → skip: already supergroup on Telegram | "
                                + _format_gc_context(
                                    club_display_name=cfg.club_display_name,
                                    club_key=cfg.club_key,
                                    title=title_out,
                                    stored_chat_id=g.chat_id,
                                    dialog_title=dialog_title,
                                    dialog_chat_id=dialog_chat_id,
                                    telegram_kind=kind,
                                    new_chat_id=new_id,
                                    status=status,
                                ),
                            )
                            results.append(
                                UpgradeRow(
                                    stored_chat_id=g.chat_id,
                                    club_id=g.club_id,
                                    club_key=cfg.club_key,
                                    club_display_name=cfg.club_display_name,
                                    title=title_out,
                                    dialog_title=dialog_title,
                                    dialog_chat_id=dialog_chat_id,
                                    telegram_kind=kind,
                                    new_chat_id=new_id,
                                    status=status,
                                )
                            )
                            already_supergroup += 1
                            continue

                        status = "would_remap_db"
                        db_rows = 0
                        if apply:
                            try:
                                counts = await asyncio.to_thread(
                                    _remap_chat_id_with_retry, g.chat_id, new_id
                                )
                                db_rows = sum(counts.values())
                                status = "db_remapped"
                                db_remapped_only += 1
                            except Exception as e:
                                status = f"db_remap_error:{_error_label(e)}"
                                db_update_failed += 1
                        else:
                            db_remapped_only += 1

                        _progress(
                            show_progress,
                            f"  → {'remapped DB' if apply and status == 'db_remapped' else 'would remap DB'} | "
                            + _format_gc_context(
                                club_display_name=cfg.club_display_name,
                                club_key=cfg.club_key,
                                title=title_out,
                                stored_chat_id=g.chat_id,
                                dialog_title=dialog_title,
                                dialog_chat_id=dialog_chat_id,
                                telegram_kind=kind,
                                new_chat_id=new_id,
                                status=status,
                            )
                            + (f" | db_rows={db_rows}" if db_rows else ""),
                        )

                        results.append(
                            UpgradeRow(
                                stored_chat_id=g.chat_id,
                                club_id=g.club_id,
                                club_key=cfg.club_key,
                                club_display_name=cfg.club_display_name,
                                title=title_out,
                                dialog_title=dialog_title,
                                dialog_chat_id=dialog_chat_id,
                                telegram_kind=kind,
                                new_chat_id=new_id,
                                status=status,
                                db_rows_updated=db_rows,
                            )
                        )
                        continue

                    if kind != "basic":
                        status = f"skipped_{kind}"
                        _progress(
                            show_progress,
                            f"  → skip | "
                            + _format_gc_context(
                                club_display_name=cfg.club_display_name,
                                club_key=cfg.club_key,
                                title=title_out,
                                stored_chat_id=g.chat_id,
                                dialog_title=dialog_title,
                                dialog_chat_id=dialog_chat_id,
                                telegram_kind=kind,
                                status=status,
                            ),
                        )
                        results.append(
                            UpgradeRow(
                                stored_chat_id=g.chat_id,
                                club_id=g.club_id,
                                club_key=cfg.club_key,
                                club_display_name=cfg.club_display_name,
                                title=title_out,
                                dialog_title=dialog_title,
                                dialog_chat_id=dialog_chat_id,
                                telegram_kind=kind,
                                new_chat_id=None,
                                status=status,
                            )
                        )
                        continue

                    pending_migrate.append((g, dialog_chat_id, title_out, dialog_title))

                migrate_total = len(pending_migrate)
                if migrate_total:
                    _progress(
                        show_progress,
                        f"Basic groups queued for migrate in {cfg.club_display_name}: {migrate_total}",
                    )

                for i, (g, dialog_chat_id, title_out, dialog_title) in enumerate(
                    pending_migrate
                ):
                    if i > 0 and migrate_delay_seconds > 0:
                        await asyncio.sleep(migrate_delay_seconds)

                    new_id: int | None = None
                    status = "would_migrate"
                    db_rows = 0

                    migrate_label = (
                        f"[migrate {i + 1}/{migrate_total}] "
                        + _format_gc_context(
                            club_display_name=cfg.club_display_name,
                            club_key=cfg.club_key,
                            title=title_out,
                            stored_chat_id=g.chat_id,
                            dialog_title=dialog_title,
                            dialog_chat_id=dialog_chat_id,
                            telegram_kind="basic",
                        )
                    )
                    if apply:
                        _progress(show_progress, migrate_label)
                        try:
                            new_id = await _migrate_basic_group(client, dialog_chat_id)
                            status = "migrated"
                            migrated += 1
                        except Exception as e:
                            err = type(e).__name__
                            _progress(
                                show_progress,
                                f"  → migrate failed: {err} | "
                                + _format_gc_context(
                                    club_display_name=cfg.club_display_name,
                                    club_key=cfg.club_key,
                                    title=title_out,
                                    stored_chat_id=g.chat_id,
                                    dialog_title=dialog_title,
                                    dialog_chat_id=dialog_chat_id,
                                    telegram_kind="basic",
                                    status=f"migrate_error:{err}",
                                ),
                            )
                            results.append(
                                UpgradeRow(
                                    stored_chat_id=g.chat_id,
                                    club_id=g.club_id,
                                    club_key=cfg.club_key,
                                    club_display_name=cfg.club_display_name,
                                    title=title_out,
                                    dialog_title=dialog_title,
                                    dialog_chat_id=dialog_chat_id,
                                    telegram_kind="basic",
                                    new_chat_id=None,
                                    status=f"migrate_error:{err}",
                                )
                            )
                            migrate_failed += 1
                            continue

                        _refresh_db_pool()
                        try:
                            counts = await asyncio.to_thread(
                                _remap_chat_id_with_retry, g.chat_id, int(new_id)
                            )
                            db_rows = sum(counts.values())
                            status = "migrated_and_remapped"
                        except Exception as e:
                            status = f"migrated_db_remap_error:{_error_label(e)}"
                            db_update_failed += 1
                            logger.exception(
                                "DB remap after migrate failed for stored_chat_id=%s new_chat_id=%s",
                                g.chat_id,
                                new_id,
                            )

                        _progress(
                            show_progress,
                            f"  → migrated | "
                            + _format_gc_context(
                                club_display_name=cfg.club_display_name,
                                club_key=cfg.club_key,
                                title=title_out,
                                stored_chat_id=g.chat_id,
                                dialog_title=dialog_title,
                                dialog_chat_id=dialog_chat_id,
                                telegram_kind="basic",
                                new_chat_id=new_id,
                                status=status,
                            )
                            + (f" | db_rows={db_rows}" if db_rows else ""),
                        )
                    else:
                        would_migrate += 1
                        _progress(
                            show_progress,
                            f"  → would migrate basic group | "
                            + _format_gc_context(
                                club_display_name=cfg.club_display_name,
                                club_key=cfg.club_key,
                                title=title_out,
                                stored_chat_id=g.chat_id,
                                dialog_title=dialog_title,
                                dialog_chat_id=dialog_chat_id,
                                telegram_kind="basic",
                                status=status,
                            ),
                        )

                    results.append(
                        UpgradeRow(
                            stored_chat_id=g.chat_id,
                            club_id=g.club_id,
                            club_key=cfg.club_key,
                            club_display_name=cfg.club_display_name,
                            title=title_out,
                            dialog_title=dialog_title,
                            dialog_chat_id=dialog_chat_id,
                            telegram_kind="basic",
                            new_chat_id=new_id,
                            status=status,
                            db_rows_updated=db_rows,
                        )
                    )
            finally:
                await client.disconnect()

    summary = UpgradeSummary(
        apply_mode=apply,
        clubs_scanned=clubs_scanned,
        groups_considered=len(groups),
        already_supergroup=already_supergroup,
        db_remapped_only=db_remapped_only,
        migrated=migrated,
        would_migrate=would_migrate,
        admin_not_in_group=admin_not_in_group,
        no_mtproto_config=no_mtproto_config,
        migrate_failed=migrate_failed,
        db_update_failed=db_update_failed,
        errors=errors,
    )
    return summary, [asdict(r) for r in results]


def _row_summary_line(r: dict[str, Any]) -> str:
    return _format_gc_context(
        club_display_name=r.get("club_display_name"),
        club_key=r.get("club_key"),
        title=r.get("title") or "",
        stored_chat_id=int(r["stored_chat_id"]),
        dialog_title=r.get("dialog_title"),
        dialog_chat_id=r.get("dialog_chat_id"),
        telegram_kind=r.get("telegram_kind"),
        new_chat_id=r.get("new_chat_id"),
        status=str(r.get("status") or ""),
    )


def _print_human(summary: UpgradeSummary, rows: list[dict[str, Any]]) -> None:
    mode = "APPLY" if summary.apply_mode else "DRY-RUN"
    print(f"\nBasic-group → supergroup upgrade ({mode}) — summary")
    print(
        f"Groups considered: {summary.groups_considered} | clubs scanned: {summary.clubs_scanned}"
    )
    print(
        f"Already supergroup: {summary.already_supergroup} | "
        f"DB remap only: {summary.db_remapped_only} | "
        f"would migrate: {summary.would_migrate} | migrated: {summary.migrated}"
    )
    print(
        f"Admin not in group: {summary.admin_not_in_group} | "
        f"no MTProto config: {summary.no_mtproto_config} | "
        f"migrate failed: {summary.migrate_failed} | "
        f"DB update failed: {summary.db_update_failed} | "
        f"errors: {summary.errors}"
    )
    print()

    actionable = [
        r
        for r in rows
        if r.get("status")
        in (
            "would_migrate",
            "would_remap_db",
            "migrated",
            "migrated_and_remapped",
            "db_remapped",
        )
    ]
    if actionable:
        print(f"--- Actions ({len(actionable)}) ---")
        for r in actionable:
            print(f"  {_row_summary_line(r)}")
            if r.get("db_rows_updated"):
                print(f"    db_rows_updated={r['db_rows_updated']}")
        print()

    skipped = [r for r in rows if r.get("status") == "admin_not_in_group"]
    if skipped:
        print(f"--- Admin not in group ({len(skipped)}) ---")
        for r in skipped[:20]:
            print(f"  {_row_summary_line(r)}")
        if len(skipped) > 20:
            print(f"  ... and {len(skipped) - 20} more")
        print()

    other = [
        r
        for r in rows
        if r.get("status")
        not in (
            "would_migrate",
            "would_remap_db",
            "migrated",
            "migrated_and_remapped",
            "db_remapped",
            "admin_not_in_group",
        )
    ]
    if other:
        print(f"--- Other ({len(other)}) ---")
        for r in other[:30]:
            print(f"  {_row_summary_line(r)}")
        if len(other) > 30:
            print(f"  ... and {len(other) - 30} more")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--club-key",
        choices=CLUB_KEYS,
        help="Limit to one /gc MTProto club profile (default: all configured clubs).",
    )
    parser.add_argument(
        "--chat-id",
        type=int,
        help="Limit to one tracked chat id (still requires admin membership).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Call MigrateChat and remap Postgres chat ids (default: report only).",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Do not run pg_dump before --apply (not recommended).",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help="Directory for database.dump (default: backups/upgrade_supergroup_<timestamp>/).",
    )
    parser.add_argument(
        "--include-supergroup-ids",
        action="store_true",
        help="Also scan chats whose stored id is already -100… (for DB-only remaps).",
    )
    parser.add_argument("--json", action="store_true", help="JSON to stdout.")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only warnings/errors on stderr (no per-group progress).",
    )
    parser.add_argument(
        "--migrate-delay",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="Pause between MigrateChat calls (default: 2).",
    )
    args = parser.parse_args()

    show_progress = not args.json and not args.quiet
    if not args.json:
        _configure_logging(quiet=args.quiet)

    if args.apply and not args.skip_backup:
        _backup_database(backup_dir=args.backup_dir, show_progress=show_progress)

    summary, rows = asyncio.run(
        _upgrade(
            club_key_filter=args.club_key,
            chat_id_filter=args.chat_id,
            apply=args.apply,
            migrate_delay_seconds=max(0.0, float(args.migrate_delay)),
            include_supergroup_ids=args.include_supergroup_ids,
            show_progress=show_progress,
        )
    )

    if args.json:
        print(json.dumps({"summary": asdict(summary), "groups": rows}, indent=2))
    else:
        _print_human(summary, rows)

    if (summary.errors or summary.migrate_failed or summary.db_update_failed) and args.apply:
        sys.exit(2)


if __name__ == "__main__":
    main()
