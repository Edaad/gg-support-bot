"""Worker batch: scan support megagroups for inactivity and resolve player entities."""

from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from club_gc_settings import (
    CLUB_GC_CONFIG,
    INACTIVE_OUTREACH_CLUB_KEYS,
    get_inactive_outreach_batch_size,
    get_inactive_outreach_first_delay_sec,
    get_inactive_outreach_history_limit,
    get_inactive_outreach_interval_sec,
    is_inactive_outreach_scan_enabled,
)
from bot.services.mtproto_group_activity import (
    DialogActivitySnapshot,
    ExternalActivityResult,
    annotate_duplicate_titles,
    compute_inactive_flags,
    merge_external_activity,
    resolve_exclude_user_ids,
    resolve_legacy_chat_id,
    last_external_message_at,
)
from bot.services.player_details import parse_tracking_title

logger = logging.getLogger(__name__)

_SCAN_COMPLETE = frozenset({"complete", "failed"})
_OUTREACH_APP: Any | None = None


@dataclass(frozen=True)
class OutreachScanRow:
    id: int
    club_key: str
    telegram_chat_id: int
    group_title: str
    legacy_chat_id: int | None
    gg_player_id: str | None


def is_scan_complete() -> bool:
    from db.connection import get_db
    from db.models import InactiveGroupOutreachControl

    with get_db() as session:
        ctrl = session.get(InactiveGroupOutreachControl, 1)
        if ctrl is None:
            return False
        return str(ctrl.scan_status or "") in _SCAN_COMPLETE


def _dialog_kind(entity) -> str | None:
    from telethon.tl.types import Channel, Chat, User

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


async def _scan_chat_activity(
    client,
    entity,
    *,
    exclude_user_ids: frozenset[int],
    history_limit: int,
) -> ExternalActivityResult:
    from bot.services.migration_group_readd import call_with_flood_retry

    async def _run():
        return await last_external_message_at(
            client,
            entity,
            exclude_user_ids=exclude_user_ids,
            history_limit=history_limit,
        )

    return await call_with_flood_retry(_run, label="inactive_outreach:last_external")


async def _dual_chat_activity(
    client,
    *,
    telegram_chat_id: int,
    legacy_chat_id: int | None,
    exclude_user_ids: frozenset[int],
    history_limit: int,
) -> tuple[ExternalActivityResult, ExternalActivityResult | None]:
    from bot.services.migration_group_readd import (
        call_with_flood_retry,
        is_entity_resolution_error,
    )

    supergroup_entity = None
    try:
        supergroup_entity = await call_with_flood_retry(
            lambda: client.get_entity(int(telegram_chat_id)),
            label=f"inactive_outreach:get_entity:{telegram_chat_id}",
        )
    except Exception as exc:
        if not is_entity_resolution_error(exc):
            raise
        return ExternalActivityResult(None, "entity_gone"), None

    supergroup = await _scan_chat_activity(
        client,
        supergroup_entity,
        exclude_user_ids=exclude_user_ids,
        history_limit=history_limit,
    )

    if legacy_chat_id is None:
        return supergroup, None

    legacy_entity = None
    try:
        legacy_entity = await call_with_flood_retry(
            lambda: client.get_entity(int(legacy_chat_id)),
            label=f"inactive_outreach:get_entity_legacy:{legacy_chat_id}",
        )
    except Exception as exc:
        if not is_entity_resolution_error(exc):
            raise
        logger.warning(
            "inactive_outreach: legacy entity gone club_chat=%s legacy=%s",
            telegram_chat_id,
            legacy_chat_id,
        )
        return supergroup, ExternalActivityResult(None, "entity_gone")

    legacy = await _scan_chat_activity(
        client,
        legacy_entity,
        exclude_user_ids=exclude_user_ids,
        history_limit=history_limit,
    )
    return supergroup, legacy


async def _discover_player_from_messages(
    client,
    cfg,
    *,
    telegram_chat_id: int,
    legacy_chat_id: int | None,
    self_id: int | None,
    history_limit: int,
) -> Any | None:
    from bot.services.migration_group_readd import (
        call_with_flood_retry,
        is_entity_resolution_error,
    )
    from bot.services.mtproto_group_player import find_latest_eligible_message_sender

    current_entity = None
    try:
        current_entity = await call_with_flood_retry(
            lambda: client.get_entity(int(telegram_chat_id)),
            label=f"inactive_outreach:player_entity:{telegram_chat_id}",
        )
    except Exception as exc:
        if not is_entity_resolution_error(exc):
            raise

    scan_limit = min(history_limit, 50)
    if current_entity is not None:
        user = await find_latest_eligible_message_sender(
            client,
            current_entity,
            cfg,
            self_id=self_id,
            limit=scan_limit,
        )
        if user is not None:
            return user

    if legacy_chat_id is None:
        return None

    old_entity = None
    try:
        old_entity = await call_with_flood_retry(
            lambda: client.get_entity(int(legacy_chat_id)),
            label=f"inactive_outreach:player_entity_legacy:{legacy_chat_id}",
        )
    except Exception as exc:
        if not is_entity_resolution_error(exc):
            raise
        return None

    return await find_latest_eligible_message_sender(
        client,
        old_entity,
        cfg,
        self_id=self_id,
        limit=scan_limit,
    )


async def _resolve_player_for_row(
    client,
    cfg,
    row: OutreachScanRow,
    *,
    self_id: int | None,
    history_limit: int,
    player_map: dict[int, tuple[int | None, str | None, str | None]],
) -> dict[str, Any]:
    from bot.services.migration_group_readd import (
        call_with_flood_retry,
        is_entity_resolution_error,
    )
    from bot.services.mtproto_group_player import format_telegram_user_display
    from scripts.triage_recovery_tier3_pending import account_check_from_resolved_user

    bound = player_map.get(int(row.telegram_chat_id))
    player_id: int | None = None
    player_username: str | None = None
    player_display_name: str | None = None
    player_source = "none"
    account_check: str | None = None
    entity_resolvable = False

    if bound and bound[0] is not None:
        player_id = int(bound[0])
        player_username = bound[1]
        player_source = "support_group_chats"

    if player_id is None:
        user = await _discover_player_from_messages(
            client,
            cfg,
            telegram_chat_id=int(row.telegram_chat_id),
            legacy_chat_id=row.legacy_chat_id,
            self_id=self_id,
            history_limit=history_limit,
        )
        if user is not None:
            uid = getattr(user, "id", None)
            if uid is not None:
                player_id = int(uid)
                display, un = format_telegram_user_display(user)
                player_display_name = display
                player_username = (un or "").lstrip("@") or None
                player_source = "message_scan"

    if player_id is None:
        return {
            "player_telegram_user_id": None,
            "player_username": None,
            "player_display_name": None,
            "player_source": "none",
            "account_check": None,
            "entity_resolvable": False,
        }

    resolved_user = None
    try:
        resolved_user = await call_with_flood_retry(
            lambda: client.get_entity(int(player_id)),
            label=f"inactive_outreach:get_player:{player_id}",
        )
    except Exception as exc:
        if not is_entity_resolution_error(exc):
            raise

    account_check = account_check_from_resolved_user(
        resolved_user,
        expected_user_id=player_id,
    )
    if resolved_user is not None and account_check == "alive":
        display, un = format_telegram_user_display(resolved_user)
        player_display_name = player_display_name or display
        player_username = player_username or ((un or "").lstrip("@") or None)
        entity_resolvable = True

    return {
        "player_telegram_user_id": player_id,
        "player_username": player_username,
        "player_display_name": player_display_name,
        "player_source": player_source,
        "account_check": account_check,
        "entity_resolvable": entity_resolvable,
    }


async def scan_outreach_row(
    client,
    cfg,
    row: OutreachScanRow,
    *,
    self_id: int | None,
    exclude_user_ids: frozenset[int],
    history_limit: int,
    player_map: dict[int, tuple[int | None, str | None, str | None]],
    resolve_player: bool = True,
) -> dict[str, Any]:
    """Scan one outreach row; return field dict for DB upsert."""

    now = datetime.now(timezone.utc)
    supergroup, legacy = await _dual_chat_activity(
        client,
        telegram_chat_id=int(row.telegram_chat_id),
        legacy_chat_id=row.legacy_chat_id,
        exclude_user_ids=exclude_user_ids,
        history_limit=history_limit,
    )
    merged = merge_external_activity(supergroup, legacy)
    inactive_90d, inactive_180d = compute_inactive_flags(
        merged.last_external_message_at,
        now=now,
    )

    payload: dict[str, Any] = {
        "last_external_message_at": merged.last_external_message_at,
        "activity_basis": merged.activity_basis,
        "last_external_supergroup_at": merged.last_external_supergroup_at,
        "activity_basis_supergroup": merged.activity_basis_supergroup,
        "last_external_legacy_at": merged.last_external_legacy_at,
        "activity_basis_legacy": merged.activity_basis_legacy,
        "activity_merged_from": merged.activity_merged_from,
        "inactive_90d": inactive_90d,
        "inactive_180d": inactive_180d,
        "scan_status": "scanned",
        "scan_error": None,
        "scanned_at": now,
    }

    if resolve_player and (inactive_90d or inactive_180d):
        payload.update(
            await _resolve_player_for_row(
                client,
                cfg,
                row,
                self_id=self_id,
                history_limit=history_limit,
                player_map=player_map,
            )
        )
    else:
        payload.update(
            {
                "player_telegram_user_id": None,
                "player_username": None,
                "player_display_name": None,
                "player_source": None,
                "account_check": None,
                "entity_resolvable": False,
            }
        )

    return payload


def claim_pending_batch(club_key: str, limit: int) -> list[OutreachScanRow]:
    from db.connection import get_db
    from db.models import InactiveGroupOutreachRow

    with get_db() as session:
        rows = (
            session.query(InactiveGroupOutreachRow)
            .filter(
                InactiveGroupOutreachRow.club_key == club_key,
                InactiveGroupOutreachRow.scan_status == "pending",
            )
            .order_by(InactiveGroupOutreachRow.id.asc())
            .limit(limit)
            .all()
        )
        return [
            OutreachScanRow(
                id=int(r.id),
                club_key=str(r.club_key),
                telegram_chat_id=int(r.telegram_chat_id),
                group_title=str(r.group_title),
                legacy_chat_id=int(r.legacy_chat_id) if r.legacy_chat_id is not None else None,
                gg_player_id=str(r.gg_player_id) if r.gg_player_id else None,
            )
            for r in rows
        ]


def persist_row_scan(row_id: int, fields: dict[str, Any]) -> None:
    from db.connection import get_db
    from db.models import InactiveGroupOutreachRow

    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = session.get(InactiveGroupOutreachRow, int(row_id))
        if row is None:
            return
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_at = now
        session.commit()


def persist_row_failed(row_id: int, error: str) -> None:
    now = datetime.now(timezone.utc)
    persist_row_scan(
        row_id,
        {
            "scan_status": "failed",
            "scan_error": error[:2000],
            "scanned_at": now,
        },
    )


def _count_pending() -> int:
    from db.connection import get_db
    from db.models import InactiveGroupOutreachRow

    with get_db() as session:
        return (
            session.query(InactiveGroupOutreachRow)
            .filter(InactiveGroupOutreachRow.scan_status == "pending")
            .count()
        )


def _refresh_control_counters() -> None:
    from db.connection import get_db
    from db.models import InactiveGroupOutreachControl, InactiveGroupOutreachRow
    from sqlalchemy import func

    now = datetime.now(timezone.utc)
    with get_db() as session:
        ctrl = session.get(InactiveGroupOutreachControl, 1)
        if ctrl is None:
            return
        total = session.query(func.count(InactiveGroupOutreachRow.id)).scalar() or 0
        scanned = (
            session.query(func.count(InactiveGroupOutreachRow.id))
            .filter(InactiveGroupOutreachRow.scan_status.in_(("scanned", "failed")))
            .scalar()
            or 0
        )
        inactive_90d = (
            session.query(func.count(InactiveGroupOutreachRow.id))
            .filter(InactiveGroupOutreachRow.inactive_90d.is_(True))
            .scalar()
            or 0
        )
        inactive_180d = (
            session.query(func.count(InactiveGroupOutreachRow.id))
            .filter(InactiveGroupOutreachRow.inactive_180d.is_(True))
            .scalar()
            or 0
        )
        entity_resolvable = (
            session.query(func.count(InactiveGroupOutreachRow.id))
            .filter(InactiveGroupOutreachRow.entity_resolvable.is_(True))
            .scalar()
            or 0
        )
        ctrl.targets_total = int(total)
        ctrl.rows_scanned = int(scanned)
        ctrl.inactive_90d_count = int(inactive_90d)
        ctrl.inactive_180d_count = int(inactive_180d)
        ctrl.entity_resolvable_count = int(entity_resolvable)
        ctrl.last_tick_at = now
        session.commit()


def _set_control_status(
    status: str,
    *,
    last_error: str | None = None,
    mark_started: bool = False,
    mark_completed: bool = False,
) -> None:
    from db.connection import get_db
    from db.models import InactiveGroupOutreachControl

    now = datetime.now(timezone.utc)
    with get_db() as session:
        ctrl = session.get(InactiveGroupOutreachControl, 1)
        if ctrl is None:
            ctrl = InactiveGroupOutreachControl(id=1)
            session.add(ctrl)
        ctrl.scan_status = status
        if mark_started and ctrl.started_at is None:
            ctrl.started_at = now
        if mark_completed:
            ctrl.completed_at = now
        if last_error is not None:
            ctrl.last_error = last_error[:2000]
        ctrl.last_tick_at = now
        session.commit()


async def seed_outreach_targets() -> int:
    """Insert one pending row per tracking-title megagroup from iter_dialogs."""

    from bot.services.mtproto_dm_gc_listener import get_listener_client
    from db.connection import get_db
    from db.models import InactiveGroupOutreachRow
    from telethon.utils import get_peer_id

    inserted = 0
    for club_key in INACTIVE_OUTREACH_CLUB_KEYS:
        cfg = CLUB_GC_CONFIG.get(club_key)
        if cfg is None:
            continue
        client = get_listener_client(club_key)
        if client is None or not client.is_connected():
            logger.warning("inactive_outreach: seed skipped club=%s (listener down)", club_key)
            continue

        club_id = int(cfg.link_club_id)
        dialogs: list[tuple[Any, str, int, str]] = []
        async for dialog in client.iter_dialogs():
            kind = _dialog_kind(dialog.entity)
            if kind is None:
                continue
            title = (dialog.title or "").strip() or "(untitled)"
            chat_id = int(get_peer_id(dialog.entity))
            dialogs.append((dialog.entity, title, chat_id, kind))

        basic_by_title: dict[str, int] = {}
        megagroup_snapshots: list[DialogActivitySnapshot] = []
        for _entity, title, chat_id, kind in dialogs:
            if kind != "basic_group":
                continue
            if not parse_tracking_title(title):
                continue
            basic_by_title[title.casefold()] = chat_id

        for _entity, title, chat_id, kind in dialogs:
            if kind != "megagroup":
                continue
            if not parse_tracking_title(title):
                continue
            megagroup_snapshots.append(
                DialogActivitySnapshot(
                    title=title,
                    chat_id=chat_id,
                    kind=kind,
                    last_message_at=None,
                    activity_basis="pending",
                )
            )

        annotated = annotate_duplicate_titles(megagroup_snapshots)
        with get_db() as session:
            for snap in annotated:
                parsed = parse_tracking_title(snap.title)
                gg_player_id = parsed[1] if parsed else None
                legacy_id = resolve_legacy_chat_id(
                    telegram_chat_id=snap.chat_id,
                    group_title=snap.title,
                    club_id=club_id,
                    basic_groups_by_title=basic_by_title,
                )
                if snap.duplicate_title and legacy_id is None:
                    legacy_id = basic_by_title.get(snap.title.casefold())

                existing = (
                    session.query(InactiveGroupOutreachRow)
                    .filter(
                        InactiveGroupOutreachRow.club_key == club_key,
                        InactiveGroupOutreachRow.telegram_chat_id == snap.chat_id,
                    )
                    .first()
                )
                if existing is not None:
                    continue

                session.add(
                    InactiveGroupOutreachRow(
                        club_key=club_key,
                        telegram_chat_id=snap.chat_id,
                        group_title=snap.title,
                        legacy_chat_id=legacy_id,
                        gg_player_id=gg_player_id,
                        duplicate_title=bool(snap.duplicate_title),
                        newer_same_title_chat_id=snap.newer_same_title_chat_id,
                        scan_status="pending",
                    )
                )
                inserted += 1
            session.commit()

        logger.info(
            "inactive_outreach: seeded club=%s megagroups=%d inserted=%d",
            club_key,
            len(annotated),
            inserted,
        )

    return inserted


async def _notify_completion_slack(*, failed: bool = False) -> None:
    from bot.services.slack_ops_notify import notify_slack_ops
    from db.connection import get_db
    from db.models import InactiveGroupOutreachControl, InactiveGroupOutreachRow
    from sqlalchemy import func

    with get_db() as session:
        ctrl = session.get(InactiveGroupOutreachControl, 1)
        if ctrl is None:
            return
        club_lines: list[str] = []
        for club_key in INACTIVE_OUTREACH_CLUB_KEYS:
            total = (
                session.query(func.count(InactiveGroupOutreachRow.id))
                .filter(InactiveGroupOutreachRow.club_key == club_key)
                .scalar()
                or 0
            )
            i90 = (
                session.query(func.count(InactiveGroupOutreachRow.id))
                .filter(
                    InactiveGroupOutreachRow.club_key == club_key,
                    InactiveGroupOutreachRow.inactive_90d.is_(True),
                )
                .scalar()
                or 0
            )
            i180 = (
                session.query(func.count(InactiveGroupOutreachRow.id))
                .filter(
                    InactiveGroupOutreachRow.club_key == club_key,
                    InactiveGroupOutreachRow.inactive_180d.is_(True),
                )
                .scalar()
                or 0
            )
            resolvable = (
                session.query(func.count(InactiveGroupOutreachRow.id))
                .filter(
                    InactiveGroupOutreachRow.club_key == club_key,
                    InactiveGroupOutreachRow.entity_resolvable.is_(True),
                )
                .scalar()
                or 0
            )
            club_lines.append(
                f"{html.escape(club_key)}: rows={total} inactive_90d={i90} "
                f"inactive_180d={i180} entity_resolvable={resolvable}"
            )

    status_label = "failed" if failed else "complete"
    detail = (
        f"<b>Inactive group outreach scan {status_label}</b>\n"
        f"targets={ctrl.targets_total} scanned={ctrl.rows_scanned}\n"
        f"inactive_90d={ctrl.inactive_90d_count} inactive_180d={ctrl.inactive_180d_count} "
        f"entity_resolvable={ctrl.entity_resolvable_count}\n"
        + "\n".join(club_lines)
    )
    if ctrl.last_error:
        detail += f"\nlast_error: {html.escape(str(ctrl.last_error)[:500])}"

    await notify_slack_ops(detail, source="inactive_group_outreach")


async def tick_async() -> dict[str, int]:
    if not is_inactive_outreach_scan_enabled():
        return {"processed": 0}

    if is_scan_complete():
        return {"processed": 0}

    summary = {"processed": 0, "failed": 0, "seeded": 0}

    from db.connection import get_db
    from db.models import InactiveGroupOutreachControl

    with get_db() as session:
        ctrl = session.get(InactiveGroupOutreachControl, 1)
        status = str(getattr(ctrl, "scan_status", None) or "idle")

    if status == "idle":
        _set_control_status("seeding", mark_started=True)
        try:
            seeded = await seed_outreach_targets()
            summary["seeded"] = seeded
            _set_control_status("scanning")
        except Exception as exc:
            logger.exception("inactive_outreach: seed failed")
            _set_control_status("failed", last_error=str(exc), mark_completed=True)
            remove_inactive_outreach_job()
            await _notify_completion_slack(failed=True)
            return summary

    batch_size = get_inactive_outreach_batch_size()
    history_limit = get_inactive_outreach_history_limit()

    from bot.services.migration_group_readd import load_player_rows_by_chat
    from bot.services.mtproto_dm_gc_listener import get_listener_client

    for club_key in INACTIVE_OUTREACH_CLUB_KEYS:
        cfg = CLUB_GC_CONFIG.get(club_key)
        if cfg is None:
            continue
        client = get_listener_client(club_key)
        if client is None or not client.is_connected():
            logger.info("inactive_outreach: skip club=%s listener down", club_key)
            continue

        rows = claim_pending_batch(club_key, batch_size)
        if not rows:
            continue

        self_id: int | None = None
        try:
            me = await client.get_me()
            if me and getattr(me, "id", None):
                self_id = int(me.id)
        except Exception:
            logger.warning("inactive_outreach: get_me failed club=%s", club_key)

        exclude_user_ids = await resolve_exclude_user_ids(client, cfg, self_id or 0)
        player_map = load_player_rows_by_chat({r.telegram_chat_id for r in rows})

        for row in rows:
            try:
                fields = await scan_outreach_row(
                    client,
                    cfg,
                    row,
                    self_id=self_id,
                    exclude_user_ids=exclude_user_ids,
                    history_limit=history_limit,
                    player_map=player_map,
                )
                persist_row_scan(row.id, fields)
                summary["processed"] += 1
            except Exception as exc:
                logger.exception(
                    "inactive_outreach: scan failed row_id=%s chat_id=%s",
                    row.id,
                    row.telegram_chat_id,
                )
                persist_row_failed(row.id, str(exc))
                summary["failed"] += 1

    _refresh_control_counters()

    if _count_pending() == 0:
        _set_control_status("complete", mark_completed=True)
        remove_inactive_outreach_job()
        await _notify_completion_slack(failed=False)
        logger.info("inactive_outreach: scan complete")

    return summary


def remove_inactive_outreach_job() -> None:
    if _OUTREACH_APP is None:
        return
    jobs = _OUTREACH_APP.job_queue.get_jobs_by_name("inactive_group_outreach")
    for job in jobs:
        job.schedule_removal()


def schedule_inactive_outreach_tick() -> None:
    """Run tick on the dm_gc listener Telethon loop (not the PTB job-queue loop)."""

    from bot.services.mtproto_dm_gc_listener import _loop_holder

    loop = _loop_holder.get("loop")
    if loop is None or not loop.is_running():
        logger.warning("inactive_outreach: listener loop not running; skipping tick")
        return
    asyncio.run_coroutine_threadsafe(tick_async(), loop)


def inactive_outreach_job_callback(context) -> None:
    schedule_inactive_outreach_tick()


def setup_inactive_group_outreach_job(app) -> None:
    """Schedule one-shot inactive outreach scan after worker boot."""

    global _OUTREACH_APP

    from club_gc_settings import is_dm_gc_listener_enabled

    if not is_dm_gc_listener_enabled():
        return
    if not is_inactive_outreach_scan_enabled():
        return
    if is_scan_complete():
        logger.info("inactive_outreach: control row complete; job not scheduled")
        return

    _OUTREACH_APP = app
    remove_inactive_outreach_job()
    interval_sec = get_inactive_outreach_interval_sec()
    first_delay_sec = get_inactive_outreach_first_delay_sec()
    app.job_queue.run_repeating(
        inactive_outreach_job_callback,
        interval=timedelta(seconds=interval_sec),
        first=timedelta(seconds=first_delay_sec),
        name="inactive_group_outreach",
    )
    logger.info(
        "inactive_outreach job scheduled first_delay_sec=%s interval_sec=%s batch_size=%s",
        first_delay_sec,
        interval_sec,
        get_inactive_outreach_batch_size(),
    )
