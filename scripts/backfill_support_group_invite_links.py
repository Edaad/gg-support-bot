"""Backfill support_group_chats.invite_link for linked groups (groups table).

Only writes when the club MTProto admin account is still in the group (the chat
appears in that club's Telethon dialog list). Dry-run by default; pass --apply
to write Postgres.

Environment: DATABASE_URL, TG_API_ID, TG_API_HASH (same as other MTProto scripts).

Usage:
  python scripts/backfill_support_group_invite_links.py
  python scripts/backfill_support_group_invite_links.py --club-key clubgto
  python scripts/backfill_support_group_invite_links.py --apply
  python scripts/backfill_support_group_invite_links.py --apply --club-key round_table --json
  python scripts/backfill_support_group_invite_links.py --chat-id -1001234567890 --apply

Telegram rate-limits bulk ExportChatInvite. This script sleeps through FloodWait
and pauses between exports (see --export-delay). Re-run safely: groups that
already have invite_link are skipped.
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

logger = logging.getLogger("backfill_support_group_invite_links")

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
class LinkedGroupRow:
    chat_id: int
    club_id: int
    title: str


@dataclass(frozen=True)
class BackfillRow:
    groups_chat_id: int
    club_id: int
    club_key: str | None
    title: str
    dialog_chat_id: int | None
    invite_link: str | None
    status: str
    row_id: int | None = None


@dataclass(frozen=True)
class BackfillSummary:
    apply_mode: bool
    clubs_scanned: int
    groups_considered: int
    already_linked: int
    admin_not_in_group: int
    no_mtproto_config: int
    export_failed: int
    inserted: int
    updated: int
    unchanged: int
    errors: int


def _is_group_dialog(dialog) -> bool:
    if dialog.is_group:
        return True
    if dialog.is_channel:
        entity = dialog.entity
        return bool(getattr(entity, "megagroup", False))
    return False


def _configure_logging(*, quiet: bool) -> None:
    level = logging.WARNING if quiet else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
        force=True,
    )


def _load_linked_groups(
    *,
    club_id: int | None,
    chat_id: int | None,
) -> list[LinkedGroupRow]:
    from db.connection import get_db
    from db.models import Group

    with get_db() as session:
        q = session.query(Group.chat_id, Group.club_id, Group.name)
        if club_id is not None:
            q = q.filter(Group.club_id == int(club_id))
        if chat_id is not None:
            q = q.filter(Group.chat_id == int(chat_id))
        rows = q.order_by(Group.club_id, Group.chat_id).all()

    out: list[LinkedGroupRow] = []
    for cid, club, name in rows:
        title = (name or "").strip()
        if not title:
            continue
        out.append(LinkedGroupRow(chat_id=int(cid), club_id=int(club), title=title))
    return out


def _find_dialog_for_group(
    group_chat_id: int,
    dialogs: list[tuple[int, str]],
) -> tuple[int, str] | None:
    from notification.chat_id import telegram_chat_ids_match

    for dialog_chat_id, dialog_title in dialogs:
        if telegram_chat_ids_match(dialog_chat_id, group_chat_id):
            return dialog_chat_id, dialog_title
    return None


async def _list_admin_group_dialogs(cfg) -> list[tuple[int, str]]:
    from bot.services.mtproto_group_create import (
        get_mtproto_lock,
        is_client_authorized,
        make_client,
    )

    if not await is_client_authorized(cfg):
        raise RuntimeError(
            f"Telethon session not authorized for club_key={cfg.club_key!r}"
        )

    dialogs: list[tuple[int, str]] = []
    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError(
                    f"Telethon not authorized after connect (club_key={cfg.club_key})"
                )
            async for dialog in client.iter_dialogs():
                if not _is_group_dialog(dialog):
                    continue
                title = (dialog.title or dialog.name or "").strip()
                dialogs.append((int(dialog.id), title))
        finally:
            await client.disconnect()
    return dialogs


async def _export_invite_link_backfill(client, peer) -> str | None:
    """Export invite link; sleep through Telegram FloodWait (no interactive cap)."""
    from telethon.errors import FloodWaitError

    from bot.services.mtproto_group_create import normalize_invite_link

    while True:
        try:
            export_fn = getattr(client, "export_chat_invite_link", None)
            if callable(export_fn):
                raw = await export_fn(peer)
            else:
                from telethon.tl import functions

                inp = await client.get_input_entity(peer)
                inv = await client(functions.messages.ExportChatInviteRequest(peer=inp))
                raw = inv.link
            return normalize_invite_link(raw)
        except FloodWaitError as e:
            logger.warning(
                "Telegram rate limit: sleeping %ss before retrying export",
                e.seconds,
            )
            await asyncio.sleep(float(e.seconds) + 2.0)
        except Exception as e:
            logger.warning("export invite failed: %s", type(e).__name__)
            return None


async def _export_invite_links_for_dialogs(
    cfg,
    dialog_chat_ids: list[int],
    *,
    export_delay_seconds: float,
) -> dict[int, str | None]:
    from bot.services.mtproto_group_create import get_mtproto_lock, make_client

    out: dict[int, str | None] = {}
    if not dialog_chat_ids:
        return out

    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg)
        await client.connect()
        try:
            for i, dialog_chat_id in enumerate(dialog_chat_ids):
                cid = int(dialog_chat_id)
                if i > 0 and export_delay_seconds > 0:
                    await asyncio.sleep(export_delay_seconds)
                try:
                    entity = await client.get_entity(cid)
                    out[cid] = await _export_invite_link_backfill(client, entity)
                except Exception as e:
                    logger.warning(
                        "export invite failed chat_id=%s: %s",
                        cid,
                        type(e).__name__,
                    )
                    out[cid] = None
        finally:
            await client.disconnect()
    return out


async def _backfill(
    *,
    club_key_filter: str | None,
    chat_id_filter: int | None,
    apply: bool,
    export_delay_seconds: float,
) -> tuple[BackfillSummary, list[dict[str, Any]]]:
    from club_gc_settings import CLUB_GC_CONFIG, get_club_gc_config_by_link_club_id
    from bot.services.support_group_chats import (
        fetch_invite_link_for_chat,
        upsert_support_group_invite_link,
    )
    from db.connection import init_engine

    init_engine()

    club_id_filter: int | None = None
    if club_key_filter:
        cfg = CLUB_GC_CONFIG.get(club_key_filter)
        if cfg is None:
            raise SystemExit(f"Unknown club_key: {club_key_filter!r}")
        club_id_filter = int(cfg.link_club_id)

    groups = _load_linked_groups(club_id=club_id_filter, chat_id=chat_id_filter)
    by_club: dict[int, list[LinkedGroupRow]] = {}
    for row in groups:
        by_club.setdefault(row.club_id, []).append(row)

    results: list[BackfillRow] = []
    already_linked = admin_not_in_group = no_mtproto_config = export_failed = 0
    inserted = updated = unchanged = errors = 0
    clubs_scanned = 0

    for club_id, club_groups in sorted(by_club.items()):
        cfg = get_club_gc_config_by_link_club_id(int(club_id))
        if cfg is None:
            for g in club_groups:
                results.append(
                    BackfillRow(
                        groups_chat_id=g.chat_id,
                        club_id=g.club_id,
                        club_key=None,
                        title=g.title,
                        dialog_chat_id=None,
                        invite_link=None,
                        status="no_mtproto_config",
                    )
                )
                no_mtproto_config += 1
            continue

        if club_key_filter and cfg.club_key != club_key_filter:
            continue

        clubs_scanned += 1
        logger.info(
            "Scanning MTProto dialogs for %s (club_id=%s, %s groups)",
            cfg.club_display_name,
            club_id,
            len(club_groups),
        )

        try:
            dialogs = await _list_admin_group_dialogs(cfg)
        except Exception as e:
            logger.error(
                "Failed to list dialogs for club_key=%s: %s",
                cfg.club_key,
                type(e).__name__,
            )
            for g in club_groups:
                results.append(
                    BackfillRow(
                        groups_chat_id=g.chat_id,
                        club_id=g.club_id,
                        club_key=cfg.club_key,
                        title=g.title,
                        dialog_chat_id=None,
                        invite_link=None,
                        status=f"dialog_scan_error:{type(e).__name__}",
                    )
                )
                errors += 1
            continue

        logger.info(
            "Found %s group dialogs for club_key=%s",
            len(dialogs),
            cfg.club_key,
        )

        pending: list[tuple[LinkedGroupRow, int, str]] = []
        for g in club_groups:
            if fetch_invite_link_for_chat(g.chat_id, group_title=g.title):
                results.append(
                    BackfillRow(
                        groups_chat_id=g.chat_id,
                        club_id=g.club_id,
                        club_key=cfg.club_key,
                        title=g.title,
                        dialog_chat_id=None,
                        invite_link=None,
                        status="already_linked",
                    )
                )
                already_linked += 1
                continue

            match = _find_dialog_for_group(g.chat_id, dialogs)
            if match is None:
                results.append(
                    BackfillRow(
                        groups_chat_id=g.chat_id,
                        club_id=g.club_id,
                        club_key=cfg.club_key,
                        title=g.title,
                        dialog_chat_id=None,
                        invite_link=None,
                        status="admin_not_in_group",
                    )
                )
                admin_not_in_group += 1
                continue

            dialog_chat_id, dialog_title = match
            title_out = g.title or dialog_title
            pending.append((g, dialog_chat_id, title_out))

        export_ids = sorted({dialog_chat_id for _, dialog_chat_id, _ in pending})
        logger.info(
            "Exporting invite links for %s matched groups (club_key=%s)",
            len(export_ids),
            cfg.club_key,
        )
        exported = await _export_invite_links_for_dialogs(
            cfg,
            export_ids,
            export_delay_seconds=export_delay_seconds,
        )

        for g, dialog_chat_id, title_out in pending:
            invite_link = exported.get(int(dialog_chat_id))
            if not invite_link:
                results.append(
                    BackfillRow(
                        groups_chat_id=g.chat_id,
                        club_id=g.club_id,
                        club_key=cfg.club_key,
                        title=title_out,
                        dialog_chat_id=dialog_chat_id,
                        invite_link=None,
                        status="export_failed",
                    )
                )
                export_failed += 1
                continue

            status = "would_upsert"
            row_id: int | None = None
            if apply:
                upsert_status, row_id = await asyncio.to_thread(
                    upsert_support_group_invite_link,
                    club_key=cfg.club_key,
                    club_display_name=cfg.club_display_name,
                    telegram_chat_id=dialog_chat_id,
                    telegram_chat_title=title_out,
                    invite_link=invite_link,
                    mtproto_session_name=cfg.mtproto_session,
                )
                status = upsert_status
                if upsert_status == "inserted":
                    inserted += 1
                elif upsert_status == "updated":
                    updated += 1
                elif upsert_status == "unchanged":
                    unchanged += 1
                else:
                    errors += 1

            results.append(
                BackfillRow(
                    groups_chat_id=g.chat_id,
                    club_id=g.club_id,
                    club_key=cfg.club_key,
                    title=title_out,
                    dialog_chat_id=dialog_chat_id,
                    invite_link=invite_link,
                    status=status,
                    row_id=row_id,
                )
            )

    summary = BackfillSummary(
        apply_mode=apply,
        clubs_scanned=clubs_scanned,
        groups_considered=len(groups),
        already_linked=already_linked,
        admin_not_in_group=admin_not_in_group,
        no_mtproto_config=no_mtproto_config,
        export_failed=export_failed,
        inserted=inserted,
        updated=updated,
        unchanged=unchanged,
        errors=errors,
    )
    return summary, [asdict(r) for r in results]


def _print_human(summary: BackfillSummary, rows: list[dict[str, Any]]) -> None:
    mode = "APPLY" if summary.apply_mode else "DRY-RUN"
    print(f"Support group invite-link backfill ({mode})")
    print(
        f"Groups considered: {summary.groups_considered} | clubs scanned: {summary.clubs_scanned}"
    )
    print(
        f"Already linked: {summary.already_linked} | admin not in group: {summary.admin_not_in_group} | "
        f"no MTProto config: {summary.no_mtproto_config} | export failed: {summary.export_failed}"
    )
    if summary.apply_mode:
        print(
            f"Applied: inserted={summary.inserted} updated={summary.updated} "
            f"unchanged={summary.unchanged} errors={summary.errors}"
        )
    print()

    actionable = [
        r
        for r in rows
        if r.get("status") in ("would_upsert", "inserted", "updated", "unchanged")
    ]
    if actionable:
        print(f"--- Invite links ({len(actionable)}) ---")
        for r in actionable:
            print(f"  groups_chat_id={r['groups_chat_id']} dialog_chat_id={r['dialog_chat_id']}")
            print(f"    title: {r['title']}")
            print(f"    link: {r.get('invite_link')}")
            print(f"    status: {r['status']}" + (f" row_id={r['row_id']}" if r.get("row_id") else ""))
        print()

    skipped = [r for r in rows if r.get("status") == "admin_not_in_group"]
    if skipped:
        print(f"--- Admin not in group ({len(skipped)}) ---")
        for r in skipped[:20]:
            print(f"  chat_id={r['groups_chat_id']} title={r['title']!r}")
        if len(skipped) > 20:
            print(f"  ... and {len(skipped) - 20} more")
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
        help="Limit to one groups.chat_id (still requires admin membership).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write support_group_chats.invite_link (default: report only).",
    )
    parser.add_argument("--json", action="store_true", help="JSON to stdout.")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only warnings/errors on stderr (no per-group progress).",
    )
    parser.add_argument(
        "--export-delay",
        type=float,
        default=3.0,
        metavar="SECONDS",
        help="Pause between ExportChatInvite calls (default: 3). Use 5+ for large runs.",
    )
    args = parser.parse_args()

    if not args.json:
        _configure_logging(quiet=args.quiet)

    summary, rows = asyncio.run(
        _backfill(
            club_key_filter=args.club_key,
            chat_id_filter=args.chat_id,
            apply=args.apply,
            export_delay_seconds=max(0.0, float(args.export_delay)),
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
