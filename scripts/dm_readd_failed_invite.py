"""Re-add players to migrated supergroups; DM + CSV when direct add fails.

Reads deposit / invite-target CSVs (default: ``gc_active_migrated_invite_targets.csv``
when present, else the same fallbacks as ``dm_deposit_groups_invite.py``). With
``--apply``, tries ``InviteToChannel`` per player. When Telegram blocks the add
(privacy or other failure), exports a fresh invite link and DMs
``PLAYER_MIGRATION_UPGRADE_INVITE_MESSAGE``.

Progress is tracked in ``backups/dm_readd_failed_invite_tracker.csv`` (override with
``--tracker-csv``). Re-runs skip chats/players already recorded as ``dm_sent``.

Players who could not be added are written to a CSV (``--failed-csv-out``, default:
``backups/readd_failed_invite_targets_<ts>.csv``) with ``player_username`` and
``invite_link`` for manual follow-up.

Dry-run by default; pass ``--apply`` to re-add and send DMs. Use ``--skip-readd`` to
only check membership, export links, and DM players who are not in the group (no
``InviteToChannel`` attempt).

Environment: DATABASE_URL, TG_API_ID, TG_API_HASH (same as other MTProto scripts).

Operational: do not run while the Heroku worker holds the same club Telethon
session. Set ``GC_MTPROTO_ENABLED=false`` (or ``GC_DM_GC_LISTENER_ENABLED=false``)
on the worker and restart before running; re-enable after.

Usage:
  python scripts/dm_readd_failed_invite.py
  python scripts/dm_readd_failed_invite.py --club-key clubgto --limit 5
  python scripts/dm_readd_failed_invite.py --apply --club-key round_table --dm-delay 3
  python scripts/dm_readd_failed_invite.py --input-csv gc_active_migrated_invite_targets.csv --apply
  python scripts/dm_readd_failed_invite.py --skip-readd --apply --export-invite-links
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("dm_readd_failed_invite")

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
    _configure_logging,
    _find_dialog_for_group,
    _gc_display_name,
    _list_admin_group_dialogs,
)
from scripts.dm_deposit_groups_invite import (  # noqa: E402
    DepositGroupTarget,
    DmResult,
    DmTracker,
    _ClubClientPool,
    _default_input_csv as _default_deposit_input_csv,
    _load_targets_from_csv as _load_deposit_targets_from_csv,
    _load_targets_from_db,
    _precheck_target,
    _resolve_club_cfg,
    _send_player_dm,
)
from scripts.readd_migrated_group_members import (  # noqa: E402
    _call_with_flood_retry,
    _export_invite_link,
    _invite_user_id,
    _load_player_rows_by_chat,
    _participant_user_ids,
)


@dataclass(frozen=True)
class FailedInviteRow:
    telegram_chat_id: int
    gc_title: str
    club_key: str
    player_telegram_user_id: int | None
    player_username: str | None
    invite_link: str | None
    readd_status: str
    error: str | None = None
    dm_status: str | None = None
    dm_seq: int | None = None
    dm_sent_at: str | None = None


@dataclass
class ReaddFailedSummary:
    apply_mode: bool
    skip_readd: bool
    targets: int
    processed: int
    readd_ok: int
    already_member: int
    could_not_add: int
    dm_sent: int
    dm_would_send: int
    no_player_id: int
    no_invite_link: int
    admin_not_in_group: int
    skipped_already_dm: int
    skipped_player_already_dm: int
    errors: int
    tracker_path: str
    tracker_dm_sent_total: int
    failed_csv_path: str
    failed_csv_rows: int


FAILED_CSV_FIELDS = [
    "player_username",
    "invite_link",
    "telegram_chat_id",
    "gc_title",
    "club_key",
    "player_telegram_user_id",
    "readd_status",
    "error",
    "dm_status",
    "dm_seq",
    "dm_sent_at",
]


def _default_input_csv() -> Path:
    invite_targets = _REPO_ROOT / "gc_active_migrated_invite_targets.csv"
    if invite_targets.is_file():
        return invite_targets
    return _default_deposit_input_csv()


def _default_failed_csv() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _REPO_ROOT / "backups" / f"readd_failed_invite_targets_{stamp}.csv"


def _default_tracker_csv() -> Path:
    return _REPO_ROOT / "backups" / "dm_readd_failed_invite_tracker.csv"


def _format_username(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if value.startswith("@"):
        return value
    if value.isdigit():
        return None
    return f"@{value.lstrip('@')}"


async def _resolve_username(client, player_id: int, stored: str | None) -> str | None:
    formatted = _format_username(stored)
    if formatted:
        return formatted
    try:
        entity = await _call_with_flood_retry(
            lambda: client.get_entity(int(player_id)),
            label="get_entity(username)",
        )
        uname = getattr(entity, "username", None)
        if uname:
            return f"@{uname}"
    except Exception:
        pass
    return str(player_id)


def _write_failed_csv(path: Path, rows: list[FailedInviteRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FAILED_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "player_username": row.player_username or "",
                    "invite_link": row.invite_link or "",
                    "telegram_chat_id": row.telegram_chat_id,
                    "gc_title": row.gc_title,
                    "club_key": row.club_key,
                    "player_telegram_user_id": row.player_telegram_user_id or "",
                    "readd_status": row.readd_status,
                    "error": row.error or "",
                    "dm_status": row.dm_status or "",
                    "dm_seq": row.dm_seq or "",
                    "dm_sent_at": row.dm_sent_at or "",
                }
            )


@dataclass
class _ProcessOutcome:
    dm_result: DmResult | None = None
    failed_row: FailedInviteRow | None = None
    readd_ok: bool = False
    already_member: bool = False
    admin_not_in_group: bool = False


async def _ensure_invite_link(
    *,
    client,
    cfg,
    target: DepositGroupTarget,
    title: str,
    invite_link: str | None,
    export_invite_links: bool,
    update_invite_links: bool,
) -> tuple[str | None, str | None]:
    """Return (invite_link, error)."""
    from bot.services.support_group_chats import (
        fetch_invite_link_for_chat,
        upsert_support_group_invite_link,
    )

    link = invite_link or fetch_invite_link_for_chat(
        target.telegram_chat_id,
        group_title=target.gc_title or None,
    )
    if link and not export_invite_links:
        return link, None

    try:
        entity = await _call_with_flood_retry(
            lambda cid=target.telegram_chat_id: client.get_entity(int(cid)),
            label="get_entity(group)",
        )
        exported = await _export_invite_link(client, entity)
        if exported:
            link = exported
            if update_invite_links:
                upsert_support_group_invite_link(
                    club_key=cfg.club_key,
                    club_display_name=cfg.club_display_name,
                    telegram_chat_id=int(target.telegram_chat_id),
                    telegram_chat_title=title,
                    invite_link=link,
                    mtproto_session_name=cfg.mtproto_session,
                )
        return link, None
    except Exception as e:
        return link, type(e).__name__


async def _process_target_dry(
    target: DepositGroupTarget,
    *,
    skip_readd: bool,
    use_tracker: bool,
    tracker: DmTracker,
    player_map: dict[int, tuple[int | None, str | None, str | None]],
    dialog_cache: dict[str, list[tuple[int, str]]],
) -> _ProcessOutcome:
    from bot.services.support_group_chats import fetch_invite_link_for_chat

    cfg = _resolve_club_cfg(target)
    club_key = cfg.club_key if cfg else (target.club_key or "?")
    early = _precheck_target(
        target,
        club_key=club_key,
        use_tracker=use_tracker,
        tracker=tracker,
        player_map=player_map,
    )
    if early is not None:
        return _ProcessOutcome(dm_result=early)

    title = _gc_display_name(target.gc_title, target.telegram_chat_id)
    player_id, player_username, _ = player_map.get(
        target.telegram_chat_id, (None, None, None)
    )
    if player_id is None:
        return _ProcessOutcome(
            dm_result=DmResult(
                telegram_chat_id=target.telegram_chat_id,
                gc_title=title,
                club_key=club_key,
                player_telegram_user_id=None,
                player_username=player_username,
                invite_link=None,
                status="no_player_id",
            )
        )

    if cfg is None:
        return _ProcessOutcome(
            dm_result=DmResult(
                telegram_chat_id=target.telegram_chat_id,
                gc_title=title,
                club_key=club_key,
                player_telegram_user_id=player_id,
                player_username=player_username,
                invite_link=None,
                status="no_mtproto_config",
            )
        )

    if cfg.club_key not in dialog_cache:
        try:
            dialog_cache[cfg.club_key] = await _list_admin_group_dialogs(cfg)
        except Exception as e:
            return _ProcessOutcome(
                dm_result=DmResult(
                    telegram_chat_id=target.telegram_chat_id,
                    gc_title=title,
                    club_key=club_key,
                    player_telegram_user_id=player_id,
                    player_username=player_username,
                    invite_link=None,
                    status="dialog_list_error",
                    error=type(e).__name__,
                )
            )

    found = _find_dialog_for_group(target.telegram_chat_id, dialog_cache[cfg.club_key])
    if found is None:
        return _ProcessOutcome(
            dm_result=DmResult(
                telegram_chat_id=target.telegram_chat_id,
                gc_title=title,
                club_key=club_key,
                player_telegram_user_id=player_id,
                player_username=player_username,
                invite_link=None,
                status="admin_not_in_group",
            ),
            admin_not_in_group=True,
        )

    invite_link = fetch_invite_link_for_chat(
        target.telegram_chat_id,
        group_title=target.gc_title or None,
    )
    readd_status = "would_check_membership"
    if skip_readd:
        readd_status = "not_in_group"
    elif not invite_link:
        return _ProcessOutcome(
            dm_result=DmResult(
                telegram_chat_id=target.telegram_chat_id,
                gc_title=title,
                club_key=club_key,
                player_telegram_user_id=player_id,
                player_username=player_username,
                invite_link=None,
                status="no_invite_link",
            )
        )

    return _ProcessOutcome(
        dm_result=DmResult(
            telegram_chat_id=target.telegram_chat_id,
            gc_title=title,
            club_key=club_key,
            player_telegram_user_id=player_id,
            player_username=player_username,
            invite_link=invite_link,
            status="would_dm",
        ),
        failed_row=FailedInviteRow(
            telegram_chat_id=target.telegram_chat_id,
            gc_title=title,
            club_key=club_key,
            player_telegram_user_id=player_id,
            player_username=_format_username(player_username),
            invite_link=invite_link,
            readd_status=readd_status,
            dm_status="would_dm",
        ),
    )


async def _process_target_apply(
    target: DepositGroupTarget,
    *,
    client_pool: _ClubClientPool,
    skip_readd: bool,
    use_tracker: bool,
    tracker: DmTracker,
    export_invite_links: bool,
    update_invite_links: bool,
    player_map: dict[int, tuple[int | None, str | None, str | None]],
    dialog_cache: dict[str, list[tuple[int, str]]],
) -> _ProcessOutcome:
    from bot.services.player_support_dm_messages import PLAYER_MIGRATION_UPGRADE_INVITE_MESSAGE
    from bot.services.support_group_chats import (
        fetch_invite_link_for_chat,
        fetch_support_group_chat_row_for_chat,
        update_support_group_chat_row,
    )
    from bot.services.mtproto_group_create import get_mtproto_lock

    cfg = _resolve_club_cfg(target)
    club_key = cfg.club_key if cfg else (target.club_key or "?")
    early = _precheck_target(
        target,
        club_key=club_key,
        use_tracker=use_tracker,
        tracker=tracker,
        player_map=player_map,
    )
    if early is not None:
        return _ProcessOutcome(dm_result=early)

    title = _gc_display_name(target.gc_title, target.telegram_chat_id)
    player_id, player_username, _ = player_map.get(
        target.telegram_chat_id, (None, None, None)
    )
    if player_id is None:
        return _ProcessOutcome(
            dm_result=DmResult(
                telegram_chat_id=target.telegram_chat_id,
                gc_title=title,
                club_key=club_key,
                player_telegram_user_id=None,
                player_username=player_username,
                invite_link=None,
                status="no_player_id",
            )
        )

    if cfg is None:
        return _ProcessOutcome(
            dm_result=DmResult(
                telegram_chat_id=target.telegram_chat_id,
                gc_title=title,
                club_key=club_key,
                player_telegram_user_id=player_id,
                player_username=player_username,
                invite_link=None,
                status="no_mtproto_config",
            )
        )

    if cfg.club_key not in dialog_cache:
        try:
            dialog_cache[cfg.club_key] = await _list_admin_group_dialogs(cfg)
        except Exception as e:
            return _ProcessOutcome(
                dm_result=DmResult(
                    telegram_chat_id=target.telegram_chat_id,
                    gc_title=title,
                    club_key=club_key,
                    player_telegram_user_id=player_id,
                    player_username=player_username,
                    invite_link=None,
                    status="dialog_list_error",
                    error=type(e).__name__,
                )
            )

    found = _find_dialog_for_group(target.telegram_chat_id, dialog_cache[cfg.club_key])
    if found is None:
        return _ProcessOutcome(
            dm_result=DmResult(
                telegram_chat_id=target.telegram_chat_id,
                gc_title=title,
                club_key=club_key,
                player_telegram_user_id=player_id,
                player_username=player_username,
                invite_link=None,
                status="admin_not_in_group",
            ),
            admin_not_in_group=True,
        )

    dialog_chat_id, _ = found
    client = await client_pool.get(cfg)
    invite_link = fetch_invite_link_for_chat(
        target.telegram_chat_id,
        group_title=target.gc_title or None,
    )

    async with get_mtproto_lock(cfg.club_key):
        try:
            entity = await _call_with_flood_retry(
                lambda: client.get_entity(int(dialog_chat_id)),
                label="get_entity(group)",
            )
            member_ids = await _participant_user_ids(client, entity)
        except Exception as e:
            return _ProcessOutcome(
                dm_result=DmResult(
                    telegram_chat_id=target.telegram_chat_id,
                    gc_title=title,
                    club_key=club_key,
                    player_telegram_user_id=player_id,
                    player_username=player_username,
                    invite_link=invite_link,
                    status="group_lookup_error",
                    error=type(e).__name__,
                )
            )

        if int(player_id) in member_ids:
            return _ProcessOutcome(
                dm_result=DmResult(
                    telegram_chat_id=target.telegram_chat_id,
                    gc_title=title,
                    club_key=club_key,
                    player_telegram_user_id=player_id,
                    player_username=player_username,
                    invite_link=invite_link,
                    status="already_member",
                ),
                already_member=True,
            )

        readd_status = "not_in_group"
        readd_error: str | None = None
        if not skip_readd:
            add_status, readd_error = await _invite_user_id(
                client,
                entity,
                int(player_id),
                apply=True,
            )
            if add_status == "added":
                return _ProcessOutcome(
                    dm_result=DmResult(
                        telegram_chat_id=target.telegram_chat_id,
                        gc_title=title,
                        club_key=club_key,
                        player_telegram_user_id=player_id,
                        player_username=player_username,
                        invite_link=invite_link,
                        status="readd_ok",
                    ),
                    readd_ok=True,
                )
            if add_status == "already_member":
                return _ProcessOutcome(
                    dm_result=DmResult(
                        telegram_chat_id=target.telegram_chat_id,
                        gc_title=title,
                        club_key=club_key,
                        player_telegram_user_id=player_id,
                        player_username=player_username,
                        invite_link=invite_link,
                        status="already_member",
                    ),
                    already_member=True,
                )
            readd_status = "privacy" if add_status == "privacy" else "add_failed"

        invite_link, export_err = await _ensure_invite_link(
            client=client,
            cfg=cfg,
            target=target,
            title=title,
            invite_link=invite_link,
            export_invite_links=export_invite_links or not invite_link,
            update_invite_links=update_invite_links,
        )
        if not invite_link:
            return _ProcessOutcome(
                dm_result=DmResult(
                    telegram_chat_id=target.telegram_chat_id,
                    gc_title=title,
                    club_key=club_key,
                    player_telegram_user_id=player_id,
                    player_username=player_username,
                    invite_link=None,
                    status="no_invite_link",
                    error=export_err,
                )
            )

        resolved_username = await _resolve_username(client, int(player_id), player_username)
        dm_body = PLAYER_MIGRATION_UPGRADE_INVITE_MESSAGE.format(
            invite_link=invite_link.strip()
        )
        dm_ok, dm_err = await _send_player_dm(client, int(player_id), dm_body)

    dm_status = "dm_sent" if dm_ok else "dm_failed"
    row = fetch_support_group_chat_row_for_chat(
        target.telegram_chat_id,
        group_title=target.gc_title or None,
        club_key=cfg.club_key,
    )
    if row is not None:
        update_support_group_chat_row(
            row.id,
            invite_link=invite_link,
            player_dm_status="readd_failed_invite_dm" + ("_failed" if not dm_ok else ""),
            last_error_message=f"player_dm:{dm_err}" if dm_err else "",
        )

    dm_result = DmResult(
        telegram_chat_id=target.telegram_chat_id,
        gc_title=title,
        club_key=club_key,
        player_telegram_user_id=player_id,
        player_username=resolved_username,
        invite_link=invite_link,
        status=dm_status,
        error=dm_err or export_err,
    )
    if dm_ok:
        dm_result = tracker.record(dm_result)

    failed_row = FailedInviteRow(
        telegram_chat_id=target.telegram_chat_id,
        gc_title=title,
        club_key=club_key,
        player_telegram_user_id=player_id,
        player_username=resolved_username,
        invite_link=invite_link,
        readd_status=readd_status,
        error=readd_error,
        dm_status=dm_status,
        dm_seq=dm_result.dm_seq,
        dm_sent_at=dm_result.dm_sent_at,
    )
    return _ProcessOutcome(dm_result=dm_result, failed_row=failed_row)


def _count_toward_send_limit(outcome: _ProcessOutcome) -> bool:
    if outcome.dm_result is None:
        return False
    return outcome.dm_result.status in (
        "dm_sent",
        "dm_failed",
        "would_dm",
    )


async def _run(
    *,
    targets: list[DepositGroupTarget],
    apply: bool,
    skip_readd: bool,
    export_invite_links: bool,
    update_invite_links: bool,
    use_tracker: bool,
    tracker: DmTracker,
    dm_delay_seconds: float,
    send_limit: int | None,
) -> tuple[ReaddFailedSummary, list[DmResult], list[FailedInviteRow]]:
    from db.connection import init_engine

    init_engine()

    chat_ids = {t.telegram_chat_id for t in targets}
    player_map = _load_player_rows_by_chat(chat_ids)
    dialog_cache: dict[str, list[tuple[int, str]]] = {}

    dm_results: list[DmResult] = []
    failed_rows: list[FailedInviteRow] = []
    summary = ReaddFailedSummary(
        apply_mode=apply,
        skip_readd=skip_readd,
        targets=len(targets),
        processed=0,
        readd_ok=0,
        already_member=0,
        could_not_add=0,
        dm_sent=0,
        dm_would_send=0,
        no_player_id=0,
        no_invite_link=0,
        admin_not_in_group=0,
        skipped_already_dm=0,
        skipped_player_already_dm=0,
        errors=0,
        tracker_path=str(tracker.path),
        tracker_dm_sent_total=tracker.dm_sent_total,
        failed_csv_path="",
        failed_csv_rows=0,
    )

    if apply:
        tracker.open_for_append()

    client_pool = _ClubClientPool()
    sends_done = 0
    try:
        for i, target in enumerate(targets):
            if send_limit is not None and sends_done >= send_limit:
                break
            if apply and i > 0 and dm_delay_seconds > 0:
                await asyncio.sleep(dm_delay_seconds)

            if apply:
                outcome = await _process_target_apply(
                    target,
                    client_pool=client_pool,
                    skip_readd=skip_readd,
                    use_tracker=use_tracker,
                    tracker=tracker,
                    export_invite_links=export_invite_links,
                    update_invite_links=update_invite_links,
                    player_map=player_map,
                    dialog_cache=dialog_cache,
                )
            else:
                outcome = await _process_target_dry(
                    target,
                    skip_readd=skip_readd,
                    use_tracker=use_tracker,
                    tracker=tracker,
                    player_map=player_map,
                    dialog_cache=dialog_cache,
                )

            if outcome.dm_result is not None:
                dm_results.append(outcome.dm_result)
            if outcome.failed_row is not None:
                failed_rows.append(outcome.failed_row)

            summary.processed += 1
            result = outcome.dm_result
            if result is None:
                continue
            if outcome.readd_ok:
                summary.readd_ok += 1
            if outcome.already_member:
                summary.already_member += 1
            if outcome.admin_not_in_group:
                summary.admin_not_in_group += 1
            if outcome.failed_row is not None:
                summary.could_not_add += 1
            if result.status == "dm_sent":
                summary.dm_sent += 1
            elif result.status == "would_dm":
                summary.dm_would_send += 1
            elif result.status == "no_player_id":
                summary.no_player_id += 1
            elif result.status == "no_invite_link":
                summary.no_invite_link += 1
            elif result.status == "skipped_already_dm":
                summary.skipped_already_dm += 1
            elif result.status == "skipped_player_already_dm":
                summary.skipped_player_already_dm += 1
            elif result.status in (
                "dm_failed",
                "export_failed",
                "no_mtproto_config",
                "dialog_list_error",
                "group_lookup_error",
                "admin_not_in_group",
            ):
                summary.errors += 1

            if _count_toward_send_limit(outcome):
                sends_done += 1
    finally:
        if apply:
            tracker.close()
        await client_pool.close_all()

    summary.tracker_dm_sent_total = tracker.dm_sent_total
    summary.failed_csv_rows = len(failed_rows)
    return summary, dm_results, failed_rows


def _print_human(
    summary: ReaddFailedSummary,
    failed_rows: list[FailedInviteRow],
    failed_csv_path: Path,
) -> None:
    mode = "APPLY" if summary.apply_mode else "DRY-RUN"
    readd_mode = "skip re-add" if summary.skip_readd else "try re-add"
    print(f"\nRe-add failed invite ({mode}, {readd_mode}) — summary")
    print(f"Targets: {summary.targets} | processed: {summary.processed}")
    print(
        f"Re-added: {summary.readd_ok} | already member: {summary.already_member} | "
        f"could not add: {summary.could_not_add}"
    )
    print(
        f"DM sent: {summary.dm_sent} | would send: {summary.dm_would_send} | "
        f"no player id: {summary.no_player_id} | no invite link: {summary.no_invite_link}"
    )
    print(
        f"Skipped chat: {summary.skipped_already_dm} | "
        f"skipped player (dup): {summary.skipped_player_already_dm} | "
        f"admin not in group: {summary.admin_not_in_group} | errors: {summary.errors}"
    )
    print(
        f"Tracker: {summary.tracker_path} ({summary.tracker_dm_sent_total} dm_sent total)"
    )
    print(f"Failed-invite CSV: {failed_csv_path} ({summary.failed_csv_rows} rows)")

    if failed_rows:
        print(f"\n--- Could not add ({min(15, len(failed_rows))} of {len(failed_rows)}) ---")
        for row in failed_rows[:15]:
            user = row.player_username or row.player_telegram_user_id or "?"
            link = (row.invite_link or "")[:50]
            print(
                f"  {row.gc_title!r} user={user} readd={row.readd_status} "
                f"dm={row.dm_status or '?'}"
            )
            if link:
                print(f"    link: {link}...")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-csv",
        type=Path,
        default=_default_input_csv(),
        help="Invite targets or deposit groups CSV.",
    )
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="Build target list from live payment tables instead of CSV.",
    )
    parser.add_argument(
        "--club-key",
        choices=CLUB_KEYS,
        help="Limit to one /gc MTProto club profile.",
    )
    parser.add_argument("--chat-id", type=int, help="Limit to one telegram chat id.")
    parser.add_argument(
        "--limit",
        type=int,
        help="DM/would-DM at most N players this run (CSV order, after skips).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Re-add, export links, and send DMs (default: dry-run).",
    )
    parser.add_argument(
        "--skip-readd",
        action="store_true",
        help="Do not call InviteToChannel; DM players who are not in the group.",
    )
    parser.add_argument(
        "--export-invite-links",
        action="store_true",
        help="With --apply, export a fresh invite link via Telethon before DM.",
    )
    parser.add_argument(
        "--update-invite-links",
        action="store_true",
        help="With --apply --export-invite-links, upsert invite_link to Postgres.",
    )
    parser.add_argument(
        "--tracker-csv",
        type=Path,
        default=_default_tracker_csv(),
        help="Append-only log of who was DM'd (default: backups/dm_readd_failed_invite_tracker.csv).",
    )
    parser.add_argument(
        "--failed-csv-out",
        type=Path,
        help="Write could-not-add rows here (default: backups/readd_failed_invite_targets_<ts>.csv).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore tracker and resend even if already dm_sent.",
    )
    parser.add_argument(
        "--dm-delay",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="Pause between groups per club session (default: 2).",
    )
    parser.add_argument("--json", action="store_true", help="JSON summary to stdout.")
    parser.add_argument("--quiet", action="store_true", help="Only warnings/errors on stderr.")
    args = parser.parse_args()

    if not args.json:
        _configure_logging(quiet=args.quiet)

    if args.from_db:
        targets = _load_targets_from_db(
            club_key_filter=args.club_key,
            chat_id_filter=args.chat_id,
        )
    else:
        targets = _load_deposit_targets_from_csv(
            args.input_csv,
            club_key_filter=args.club_key,
            chat_id_filter=args.chat_id,
        )

    if not targets:
        raise SystemExit("No targets matched filters.")

    tracker = DmTracker(args.tracker_csv.resolve())
    tracker.load()
    use_tracker = not bool(args.force)

    summary, dm_results, failed_rows = asyncio.run(
        _run(
            targets=targets,
            apply=bool(args.apply),
            skip_readd=bool(args.skip_readd),
            export_invite_links=bool(args.export_invite_links),
            update_invite_links=bool(args.update_invite_links),
            use_tracker=use_tracker,
            tracker=tracker,
            dm_delay_seconds=max(0.0, float(args.dm_delay)),
            send_limit=args.limit,
        )
    )

    failed_csv_path = args.failed_csv_out or _default_failed_csv()
    _write_failed_csv(failed_csv_path, failed_rows)
    summary.failed_csv_path = str(failed_csv_path)

    if args.json:
        print(
            json.dumps(
                {
                    "summary": asdict(summary),
                    "failed_csv": str(failed_csv_path),
                    "failed_rows": [asdict(r) for r in failed_rows],
                    "dm_results": [asdict(r) for r in dm_results],
                },
                indent=2,
            )
        )
    else:
        _print_human(summary, failed_rows, failed_csv_path)

    if summary.errors and args.apply:
        sys.exit(2)


if __name__ == "__main__":
    main()
