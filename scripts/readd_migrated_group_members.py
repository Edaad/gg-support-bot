"""Re-add players to supergroups after a bad MigrateChat batch.

When ``upgrade_groups_to_supergroup.py`` drops members, this script direct-adds
the player via Telethon ``InviteToChannel`` (same API as ``/gc``). Staff/bot
accounts from ``GC_USERS_TO_INVITE`` are skipped unless ``--invite-staff``.
Users blocked by Telegram privacy settings are written to a CSV with a fresh
invite link.

**Affected groups** come from the earliest local ``backups/upgrade_supergroup_*/database.dump``
(override with ``--backup``): chats that were **basic groups** in that snapshot and are
**supergroups** in live Postgres now (read-only title/club match).

Live DB is **read-only** unless ``--apply --update-invite-links`` (privacy fallback only).

Environment: DATABASE_URL (read-only lookup), TG_API_ID, TG_API_HASH, pg_restore on PATH.

Operational: do not run while the Heroku worker holds the same club Telethon
session. Set ``GC_MTPROTO_ENABLED=false`` (or ``GC_DM_GC_LISTENER_ENABLED=false``)
on the worker and restart before running; re-enable after.

Usage:
  python scripts/readd_migrated_group_members.py --list-affected
  python scripts/readd_migrated_group_members.py --list-affected --club-key creator_club
  python scripts/readd_migrated_group_members.py --club-key round_table
  python scripts/readd_migrated_group_members.py --chat-id -1003959541011 --apply
  python scripts/readd_migrated_group_members.py --apply --club-key clubgto --invite-delay 3
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("readd_migrated_group_members")

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from scripts.backup_groups_reader import (  # noqa: E402
    AffectedMigratedGroup,
    basic_groups_from_backup,
    find_earliest_upgrade_backup,
    resolve_affected_from_backup,
)
from bot.services.migration_group_readd import (  # noqa: E402
    ReaddGroupResult,
    call_with_flood_retry as _call_with_flood_retry,
    error_label as _error_label,
    load_player_rows_by_chat as _load_player_rows_by_chat,
    participant_count as _participant_count,
    readd_group as _readd_group,
)
from scripts.backfill_support_group_invite_links import (  # noqa: E402
    CLUB_KEYS,
    LinkedGroupRow,
    _configure_logging,
    _find_dialog_for_group,
    _gc_display_name,
    _list_admin_group_dialogs,
)


@dataclass
class ReaddSummary:
    apply_mode: bool
    backup_path: str
    backup_basic_groups: int
    migrated_in_live: int
    not_migrated_yet: int
    missing_in_live_db: int
    clubs_scanned: int
    groups_considered: int
    groups_needing_readd: int
    groups_processed: int
    admin_not_in_group: int
    no_player_id: int
    player_added: int
    player_already_member: int
    player_privacy: int
    staff_added: int
    staff_already_member: int
    staff_privacy: int
    errors: int


@dataclass(frozen=True)
class PrivacyCsvRow:
    club_key: str
    club_display_name: str
    group_title: str
    telegram_chat_id: int
    user_kind: str
    user_marker: str
    player_telegram_user_id: int | None
    reason: str
    invite_link: str | None


def _default_csv_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _REPO_ROOT / "backups" / f"readd_privacy_fallback_{stamp}.csv"


def _progress(show: bool, msg: str) -> None:
    logger.info("%s", msg)
    if show:
        print(msg, flush=True)


def _privacy_csv_rows(
    result: ReaddGroupResult,
    *,
    club_display_name: str,
    player_id: int | None,
) -> list[PrivacyCsvRow]:
    rows: list[PrivacyCsvRow] = []
    for label in result.privacy_blocked:
        kind, _, marker = label.partition(":")
        rows.append(
            PrivacyCsvRow(
                club_key=result.club_key or "",
                club_display_name=club_display_name,
                group_title=result.title,
                telegram_chat_id=int(result.chat_id),
                user_kind=kind,
                user_marker=marker,
                player_telegram_user_id=player_id if kind == "player" else None,
                reason="privacy_restricted",
                invite_link=result.invite_link,
            )
        )
    for entry in result.failed:
        if "privacy" not in entry.lower():
            continue
        kind, _, rest = entry.partition(":")
        marker = rest.split(":", 1)[0]
        rows.append(
            PrivacyCsvRow(
                club_key=result.club_key or "",
                club_display_name=club_display_name,
                group_title=result.title,
                telegram_chat_id=int(result.chat_id),
                user_kind=kind,
                user_marker=marker,
                player_telegram_user_id=player_id if kind == "player" else None,
                reason=rest,
                invite_link=result.invite_link,
            )
        )
    return rows


def _write_privacy_csv(path: Path, rows: list[PrivacyCsvRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "club_key",
        "club_display_name",
        "group_title",
        "telegram_chat_id",
        "user_kind",
        "user_marker",
        "player_telegram_user_id",
        "reason",
        "invite_link",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _resolve_backup_path(backup_path: Path | None) -> Path:
    if backup_path is not None:
        return backup_path.resolve()
    return find_earliest_upgrade_backup(_REPO_ROOT).resolve()


def _affected_groups_from_backup(
    backup_path: Path,
    *,
    club_id_filter: int | None,
    chat_id_filter: int | None,
    mtproto_club_ids: frozenset[int],
) -> list[AffectedMigratedGroup]:
    return resolve_affected_from_backup(
        backup_path,
        mtproto_club_ids=mtproto_club_ids,
        club_id_filter=club_id_filter,
        chat_id_filter=chat_id_filter,
    )


def _migrated_linked_rows(affected: list[AffectedMigratedGroup]) -> list[LinkedGroupRow]:
    rows: list[LinkedGroupRow] = []
    for item in affected:
        if item.status != "migrated" or item.current_chat_id is None:
            continue
        rows.append(
            LinkedGroupRow(
                chat_id=int(item.current_chat_id),
                club_id=int(item.club_id),
                title=(item.title or "").strip(),
            )
        )
    return rows


def _default_affected_csv_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _REPO_ROOT / "backups" / f"affected_migrated_groups_{stamp}.csv"


def _write_affected_csv(path: Path, rows: list[AffectedMigratedGroup]) -> None:
    from club_gc_settings import get_club_gc_config_by_link_club_id

    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "club_id",
        "club_key",
        "group_title",
        "old_chat_id",
        "current_chat_id",
        "status",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            cfg = get_club_gc_config_by_link_club_id(int(row.club_id))
            writer.writerow(
                {
                    "club_id": row.club_id,
                    "club_key": cfg.club_key if cfg else "",
                    "group_title": row.title,
                    "old_chat_id": row.old_chat_id,
                    "current_chat_id": row.current_chat_id or "",
                    "status": row.status,
                }
            )


def list_affected_groups(
    *,
    backup_path: Path,
    club_key_filter: str | None,
    chat_id_filter: int | None,
    affected_csv: Path | None,
    show_progress: bool,
) -> tuple[Path, list[AffectedMigratedGroup]]:
    from club_gc_settings import CLUB_GC_CONFIG
    from db.connection import init_engine

    init_engine()
    club_id_filter: int | None = None
    if club_key_filter:
        cfg = CLUB_GC_CONFIG.get(club_key_filter)
        if cfg is None:
            raise SystemExit(f"Unknown club_key: {club_key_filter!r}")
        club_id_filter = int(cfg.link_club_id)

    mtproto_club_ids = frozenset(int(cfg.link_club_id) for cfg in CLUB_GC_CONFIG.values())
    basics = basic_groups_from_backup(backup_path)
    if club_id_filter is not None:
        basics = [b for b in basics if b.club_id == int(club_id_filter)]
    basics = [b for b in basics if b.club_id in mtproto_club_ids]

    affected = _affected_groups_from_backup(
        backup_path,
        club_id_filter=club_id_filter,
        chat_id_filter=chat_id_filter,
        mtproto_club_ids=mtproto_club_ids,
    )
    out_path = affected_csv or _default_affected_csv_path()
    _write_affected_csv(out_path, affected)

    migrated = sum(1 for a in affected if a.status == "migrated")
    pending = sum(1 for a in affected if a.status == "not_migrated_yet")
    missing = sum(1 for a in affected if a.status == "missing_in_live_db")
    _progress(
        show_progress,
        f"Backup {backup_path.name}: {len(basics)} basic groups in snapshot | "
        f"migrated={migrated} not_migrated_yet={pending} missing_in_live_db={missing}",
    )
    _progress(show_progress, f"Wrote affected-groups CSV: {out_path}")
    return out_path, affected


async def _readd(
    *,
    backup_path: Path,
    club_key_filter: str | None,
    chat_id_filter: int | None,
    apply: bool,
    update_invite_links: bool,
    invite_staff: bool,
    invite_delay_seconds: float,
    show_progress: bool,
) -> tuple[ReaddSummary, list[ReaddGroupResult], list[PrivacyCsvRow]]:
    from club_gc_settings import CLUB_GC_CONFIG, get_club_gc_config_by_link_club_id
    from bot.services.mtproto_group_create import get_mtproto_lock, make_client
    from db.connection import init_engine

    init_engine()

    club_id_filter: int | None = None
    if club_key_filter:
        cfg = CLUB_GC_CONFIG.get(club_key_filter)
        if cfg is None:
            raise SystemExit(f"Unknown club_key: {club_key_filter!r}")
        club_id_filter = int(cfg.link_club_id)

    mtproto_club_ids = frozenset(int(cfg.link_club_id) for cfg in CLUB_GC_CONFIG.values())
    basics = basic_groups_from_backup(backup_path)
    if club_id_filter is not None:
        basics = [b for b in basics if b.club_id == int(club_id_filter)]
    basics = [b for b in basics if b.club_id in mtproto_club_ids]

    affected = _affected_groups_from_backup(
        backup_path,
        club_id_filter=club_id_filter,
        chat_id_filter=chat_id_filter,
        mtproto_club_ids=mtproto_club_ids,
    )
    club_groups = _migrated_linked_rows(affected)
    player_map = _load_player_rows_by_chat({g.chat_id for g in club_groups})

    mode = "APPLY" if apply else "DRY-RUN"
    _progress(
        show_progress,
        f"Re-add migrated members ({mode}) from backup {backup_path.name}: "
        f"{len(basics)} basic in snapshot → {len(club_groups)} migrated in live DB",
    )

    results: list[ReaddGroupResult] = []
    privacy_rows: list[PrivacyCsvRow] = []
    summary = ReaddSummary(
        apply_mode=apply,
        backup_path=str(backup_path),
        backup_basic_groups=len(basics),
        migrated_in_live=sum(1 for a in affected if a.status == "migrated"),
        not_migrated_yet=sum(1 for a in affected if a.status == "not_migrated_yet"),
        missing_in_live_db=sum(1 for a in affected if a.status == "missing_in_live_db"),
        clubs_scanned=0,
        groups_considered=len(club_groups),
        groups_needing_readd=len(club_groups),
        groups_processed=0,
        admin_not_in_group=0,
        no_player_id=0,
        player_added=0,
        player_already_member=0,
        player_privacy=0,
        staff_added=0,
        staff_already_member=0,
        staff_privacy=0,
        errors=0,
    )

    by_club: dict[int, list[LinkedGroupRow]] = {}
    for row in club_groups:
        by_club.setdefault(row.club_id, []).append(row)

    for club_id, club_groups in sorted(by_club.items()):
        cfg = get_club_gc_config_by_link_club_id(int(club_id))
        if cfg is None:
            for g in club_groups:
                results.append(
                    ReaddGroupResult(
                        chat_id=g.chat_id,
                        club_id=g.club_id,
                        club_key=None,
                        title=_gc_display_name(g.title, g.chat_id),
                        member_count_before=0,
                        member_count_after=None,
                        status="no_mtproto_config",
                    )
                )
            continue

        summary.clubs_scanned += 1
        try:
            dialogs = await _list_admin_group_dialogs(cfg)
        except Exception as e:
            err = _error_label(e)
            logger.exception("list dialogs failed club_key=%s", cfg.club_key)
            for g in club_groups:
                results.append(
                    ReaddGroupResult(
                        chat_id=g.chat_id,
                        club_id=g.club_id,
                        club_key=cfg.club_key,
                        title=_gc_display_name(g.title, g.chat_id),
                        member_count_before=0,
                        member_count_after=None,
                        status="dialog_list_error",
                        error=err,
                    )
                )
                summary.errors += 1
            continue

        async with get_mtproto_lock(cfg.club_key):
            client = make_client(cfg)
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    raise RuntimeError(f"Telethon not authorized (club_key={cfg.club_key})")
                me = await client.get_me()
                listener_user_id = int(me.id) if me and getattr(me, "id", None) else None

                pending: list[tuple[LinkedGroupRow, int, int | None, str | None, int]] = []
                for g in club_groups:
                    found = _find_dialog_for_group(g.chat_id, dialogs)
                    if found is None:
                        summary.admin_not_in_group += 1
                        results.append(
                            ReaddGroupResult(
                                chat_id=g.chat_id,
                                club_id=g.club_id,
                                club_key=cfg.club_key,
                                title=_gc_display_name(g.title, g.chat_id),
                                member_count_before=0,
                                member_count_after=None,
                                status="admin_not_in_group",
                            )
                        )
                        continue

                    dialog_chat_id, _dialog_title = found
                    player_id, player_username, _club_key = player_map.get(
                        g.chat_id, (None, None, None)
                    )
                    if player_id is None:
                        summary.no_player_id += 1

                    try:
                        entity = await client.get_entity(int(dialog_chat_id))
                        count = await _participant_count(client, entity)
                    except Exception as e:
                        results.append(
                            ReaddGroupResult(
                                chat_id=g.chat_id,
                                club_id=g.club_id,
                                club_key=cfg.club_key,
                                title=_gc_display_name(g.title, g.chat_id),
                                member_count_before=0,
                                member_count_after=None,
                                status="count_error",
                                error=_error_label(e),
                            )
                        )
                        summary.errors += 1
                        continue

                    pending.append(
                        (g, dialog_chat_id, player_id, player_username, count)
                    )

                total = len(pending)
                for i, (g, dialog_chat_id, player_id, player_username, count_before) in enumerate(
                    pending
                ):
                    if i > 0 and invite_delay_seconds > 0:
                        await asyncio.sleep(invite_delay_seconds)

                    label = _gc_display_name(g.title, g.chat_id)
                    _progress(
                        show_progress,
                        f"[{i + 1}/{total}] {label} (members={count_before})",
                    )
                    result = await _readd_group(
                        client=client,
                        cfg=cfg,
                        group=g,
                        dialog_chat_id=dialog_chat_id,
                        player_id=player_id,
                        player_username=player_username,
                        apply=apply,
                        update_invite_links=update_invite_links,
                        invite_staff=invite_staff,
                        listener_user_id=listener_user_id,
                    )
                    results.append(result)
                    summary.groups_processed += 1

                    for entry in result.added:
                        if entry.startswith("would_add:"):
                            entry = entry[len("would_add:") :]
                        kind = entry.split(":", 1)[0]
                        if kind == "player":
                            summary.player_added += 1
                        else:
                            summary.staff_added += 1
                    for entry in result.already_member:
                        kind = entry.split(":", 1)[0]
                        if kind == "player":
                            summary.player_already_member += 1
                        else:
                            summary.staff_already_member += 1
                    for entry in result.privacy_blocked:
                        kind = entry.split(":", 1)[0]
                        if kind == "player":
                            summary.player_privacy += 1
                        else:
                            summary.staff_privacy += 1
                    if result.status == "error":
                        summary.errors += 1

                    privacy_rows.extend(
                        _privacy_csv_rows(
                            result,
                            club_display_name=cfg.club_display_name,
                            player_id=player_id,
                        )
                    )

                    if show_progress:
                        after = result.member_count_after
                        after_s = str(after) if after is not None else "?"
                        _progress(
                            show_progress,
                            f"  → {result.status} | before={result.member_count_before} "
                            f"after={after_s} added={len(result.added)} "
                            f"privacy={len(result.privacy_blocked)} failed={len(result.failed)}",
                        )
            finally:
                await client.disconnect()

    return summary, results, privacy_rows


def _print_human(summary: ReaddSummary, results: list[ReaddGroupResult], csv_path: Path | None) -> None:
    mode = "APPLY" if summary.apply_mode else "DRY-RUN"
    print(f"\nRe-add migrated members ({mode}) — summary")
    print(f"Backup: {summary.backup_path}")
    print(
        f"Snapshot basic groups: {summary.backup_basic_groups} | "
        f"migrated in live: {summary.migrated_in_live} | "
        f"not migrated yet: {summary.not_migrated_yet} | "
        f"missing in live DB: {summary.missing_in_live_db}"
    )
    print(
        f"To re-add: {summary.groups_needing_readd} | processed: {summary.groups_processed} | "
        f"admin not in group: {summary.admin_not_in_group}"
    )
    print(
        f"Players: added={summary.player_added} already_member={summary.player_already_member} "
        f"privacy={summary.player_privacy} | no player id in DB: {summary.no_player_id}"
    )
    print(
        f"Staff/bot: added={summary.staff_added} already_member={summary.staff_already_member} "
        f"privacy={summary.staff_privacy} | errors: {summary.errors}"
    )
    if csv_path is not None:
        print(f"Privacy fallback CSV: {csv_path}")

    interesting = [
        r
        for r in results
        if r.status not in ("admin_not_in_group", "no_mtproto_config")
    ]
    if interesting:
        print(f"\n--- Groups ({len(interesting)}) ---")
        for r in interesting[:40]:
            print(
                f"  {r.title!r} chat_id={r.chat_id} status={r.status} "
                f"members={r.member_count_before}→{r.member_count_after}"
            )
            if r.added:
                print(f"    added: {', '.join(r.added[:6])}")
            if r.privacy_blocked:
                print(f"    privacy: {', '.join(r.privacy_blocked[:6])}")
            if r.failed:
                print(f"    failed: {', '.join(r.failed[:3])}")
            if r.error:
                print(f"    error: {r.error}")
        if len(interesting) > 40:
            print(f"  ... and {len(interesting) - 40} more")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backup",
        type=Path,
        help="pg_dump from before migrate (default: earliest backups/upgrade_supergroup_*/database.dump).",
    )
    parser.add_argument(
        "--list-affected",
        action="store_true",
        help="Only parse backup + read-only live mapping; write affected-groups CSV and exit.",
    )
    parser.add_argument(
        "--affected-csv",
        type=Path,
        help="Output path for --list-affected (default: backups/affected_migrated_groups_<ts>.csv).",
    )
    parser.add_argument(
        "--club-key",
        choices=CLUB_KEYS,
        help="Limit to one /gc MTProto club profile (default: all configured clubs).",
    )
    parser.add_argument(
        "--chat-id",
        type=int,
        help="Limit to one chat id (old basic or new supergroup).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Direct-add members via Telethon (default: dry-run only).",
    )
    parser.add_argument(
        "--update-invite-links",
        action="store_true",
        help="On privacy fallback with --apply, upsert invite_link to live DB (default: off).",
    )
    parser.add_argument(
        "--invite-staff",
        action="store_true",
        help="Also direct-add GC_USERS_TO_INVITE staff/bots (default: players only).",
    )
    parser.add_argument(
        "--invite-delay",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="Pause between groups (default: 2).",
    )
    parser.add_argument(
        "--csv-out",
        type=Path,
        help="Write privacy-blocked users here (default: backups/readd_privacy_fallback_<ts>.csv).",
    )
    parser.add_argument("--json", action="store_true", help="JSON summary to stdout.")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only warnings/errors on stderr (no per-group progress).",
    )
    args = parser.parse_args()

    show_progress = not args.json and not args.quiet
    if not args.json:
        _configure_logging(quiet=args.quiet)

    backup_path = _resolve_backup_path(args.backup)

    if args.list_affected:
        out_path, affected = list_affected_groups(
            backup_path=backup_path,
            club_key_filter=args.club_key,
            chat_id_filter=args.chat_id,
            affected_csv=args.affected_csv,
            show_progress=show_progress,
        )
        if args.json:
            print(
                json.dumps(
                    {
                        "backup_path": str(backup_path),
                        "affected_csv": str(out_path),
                        "rows": [asdict(r) for r in affected],
                    },
                    indent=2,
                )
            )
        return

    summary, results, privacy_rows = asyncio.run(
        _readd(
            backup_path=backup_path,
            club_key_filter=args.club_key,
            chat_id_filter=args.chat_id,
            apply=args.apply,
            update_invite_links=bool(args.update_invite_links),
            invite_staff=bool(args.invite_staff),
            invite_delay_seconds=max(0.0, float(args.invite_delay)),
            show_progress=show_progress,
        )
    )

    csv_path: Path | None = None
    if privacy_rows:
        csv_path = args.csv_out or _default_csv_path()
        _write_privacy_csv(csv_path, privacy_rows)
    elif args.csv_out:
        csv_path = args.csv_out
        _write_privacy_csv(csv_path, [])

    if args.json:
        payload: dict[str, Any] = {
            "summary": asdict(summary),
            "groups": [asdict(r) for r in results],
            "privacy_csv": str(csv_path) if csv_path else None,
            "privacy_rows": [asdict(r) for r in privacy_rows],
        }
        print(json.dumps(payload, indent=2))
    else:
        _print_human(summary, results, csv_path)

    if summary.errors and args.apply:
        sys.exit(2)


if __name__ == "__main__":
    main()
