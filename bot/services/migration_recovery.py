"""Worker cron: batch direct-add for migrated supergroup recovery queue."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from club_gc_settings import (
    get_club_gc_config_by_link_club_id,
    get_migration_recovery_batch_size,
    get_migration_recovery_invite_delay_sec,
    is_migration_recovery_enabled,
)
from bot.services.migration_group_readd import (
    ReaddGroupResult,
    set_flood_wait_observer,
    readd_group,
)
from bot.services.migration_recovery_priority import (
    classify_priority_tier,
    compute_priority_rank,
)
from scripts.backfill_support_group_invite_links import LinkedGroupRow

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset(
    {"complete", "privacy_blocked", "failed", "skipped"}
)


@dataclass(frozen=True)
class RecoveryRow:
    id: int
    telegram_chat_id: int
    club_key: str
    club_id: int
    group_title: str
    old_chat_id: int
    player_telegram_user_id: int | None
    player_username: str | None


def map_readd_status(result: ReaddGroupResult) -> tuple[str, str | None]:
    """Map ReaddGroupResult to (readd_status, last_error)."""
    if result.privacy_blocked:
        return "privacy_blocked", None
    if result.status == "no_targets":
        return "skipped", "no_targets"
    if result.status == "error":
        return "failed", result.error or "error"
    if result.failed:
        return "failed", "; ".join(result.failed[:3])
    if result.status in ("ok", "would_readd", "partial"):
        return "complete", None
    return "failed", result.status


def build_readd_result_payload(result: ReaddGroupResult) -> dict[str, Any]:
    return {
        "inner_status": result.status,
        "added": list(result.added),
        "already_member": list(result.already_member),
        "privacy_blocked": list(result.privacy_blocked),
        "failed": list(result.failed),
        "member_count_before": result.member_count_before,
        "member_count_after": result.member_count_after,
        "error": result.error,
    }


def _format_account_lines(entries: list[str]) -> list[str]:
    lines: list[str] = []
    for entry in entries:
        label = entry
        if label.startswith("would_add:"):
            label = label[len("would_add:") :]
        if ":" in label:
            _kind, _, marker = label.partition(":")
            lines.append(marker.strip() or label)
        else:
            lines.append(label)
    return lines


def format_readd_admin_notification(
    *,
    row: RecoveryRow,
    result: ReaddGroupResult,
    terminal_status: str,
    club_display_name: str,
) -> str:
    """Human-readable DM for the club GC admin after a migration re-add attempt."""

    added = _format_account_lines(result.added)
    already = _format_account_lines(result.already_member)
    privacy = _format_account_lines(result.privacy_blocked)
    failed = _format_account_lines(result.failed)

    def _section(title: str, items: list[str]) -> str:
        if not items:
            return f"{title}: (none)"
        return title + ":\n" + "\n".join(f"  • {item}" for item in items)

    parts = [
        f"[{club_display_name}] Migration re-add attempted",
        f"GC: {row.group_title}",
        f"chat_id={row.telegram_chat_id}",
        f"Result: {terminal_status}",
        _section("Added", added),
        _section("Already in group", already),
        _section("Privacy blocked", privacy),
        _section("Failed", failed),
    ]
    if result.error:
        parts.append(f"Error: {result.error}")
    if result.invite_link:
        parts.append(f"Invite link: {result.invite_link}")
    return "\n".join(parts)


async def notify_readd_admin_dm(
    cfg,
    *,
    row: RecoveryRow,
    result: ReaddGroupResult,
    terminal_status: str,
) -> None:
    from bot.services.mtproto_track_contact import notify_club_gc_admin_dm

    text = format_readd_admin_notification(
        row=row,
        result=result,
        terminal_status=terminal_status,
        club_display_name=cfg.club_display_name,
    )
    await notify_club_gc_admin_dm(cfg, text)


def should_notify_rt_ops(
    terminal_status: str,
    result: ReaddGroupResult,
    *,
    pre_error: str | None = None,
) -> bool:
    if terminal_status == "failed":
        return True
    err_blob = " ".join(
        filter(
            None,
            [pre_error, result.error, "; ".join(result.failed[:5])],
        )
    ).lower()
    rate_markers = ("floodwait", "flood wait", "retryafter", "rate limit", "too many requests")
    return any(marker in err_blob for marker in rate_markers)


def format_rt_ops_notification(
    *,
    issue_kind: str,
    detail: str,
    row: RecoveryRow | None = None,
) -> str:
    lines = [f"Issue: {issue_kind}", detail]
    if row is not None:
        lines.extend(
            [
                f"GC: {row.group_title}",
                f"chat_id={row.telegram_chat_id}",
                f"club={row.club_key}",
            ]
        )
    return "\n".join(lines)


async def notify_rt_ops_issue(
    issue_kind: str,
    detail: str,
    *,
    row: RecoveryRow | None = None,
) -> None:
    from bot.services.mtproto_track_contact import notify_rt_support_admin_dm

    text = format_rt_ops_notification(
        issue_kind=issue_kind,
        detail=detail,
        row=row,
    )
    await notify_rt_support_admin_dm(text)


async def _notify_rt_ops_if_needed(
    *,
    row: RecoveryRow,
    result: ReaddGroupResult,
    terminal_status: str,
    pre_error: str | None = None,
) -> None:
    if not should_notify_rt_ops(terminal_status, result, pre_error=pre_error):
        return
    detail = pre_error or result.error or terminal_status
    if result.failed:
        detail = f"{detail}\nFailures: {'; '.join(result.failed[:5])}"
    try:
        await notify_rt_ops_issue(
            issue_kind=terminal_status,
            detail=detail,
            row=row,
        )
    except Exception:
        logger.warning(
            "migration_recovery: RT ops DM failed row_id=%s chat_id=%s",
            row.id,
            row.telegram_chat_id,
            exc_info=True,
        )


def claim_pending_batch(limit: int | None = None) -> list[RecoveryRow]:
    from sqlalchemy import select

    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    batch_size = int(limit or get_migration_recovery_batch_size())
    now = datetime.now(timezone.utc)
    rows: list[RecoveryRow] = []

    with get_db() as session:
        ids = list(
            session.execute(
                select(MigratedGroupRecovery.id)
                .where(MigratedGroupRecovery.readd_status == "pending")
                .order_by(
                    MigratedGroupRecovery.priority_tier.asc(),
                    MigratedGroupRecovery.priority_rank.asc(),
                )
                .with_for_update(skip_locked=True)
                .limit(batch_size)
            )
            .scalars()
            .all()
        )
        if not ids:
            return []

        for row_id in ids:
            row = session.get(MigratedGroupRecovery, int(row_id))
            if row is None:
                continue
            row.readd_status = "processing"
            row.readd_attempted_at = now
            row.updated_at = now
        session.commit()

        detail_rows = (
            session.query(MigratedGroupRecovery)
            .filter(MigratedGroupRecovery.id.in_(ids))
            .order_by(
                MigratedGroupRecovery.priority_tier.asc(),
                MigratedGroupRecovery.priority_rank.asc(),
            )
            .all()
        )

    for row in detail_rows:
        rows.append(
            RecoveryRow(
                id=int(row.id),
                telegram_chat_id=int(row.telegram_chat_id),
                club_key=str(row.club_key),
                club_id=int(row.club_id),
                group_title=str(row.group_title),
                old_chat_id=int(row.old_chat_id),
                player_telegram_user_id=(
                    int(row.player_telegram_user_id)
                    if row.player_telegram_user_id is not None
                    else None
                ),
                player_username=(row.player_username or None),
            )
        )
    return rows


def finalize_row(
    row_id: int,
    result: ReaddGroupResult,
    *,
    pre_status: str | None = None,
    pre_error: str | None = None,
) -> str:
    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    if pre_status is not None:
        status = pre_status
        last_error = pre_error
        payload = {"pre_finalize": True, "error": pre_error}
        invite_link = None
    else:
        status, last_error = map_readd_status(result)
        payload = build_readd_result_payload(result)
        invite_link = result.invite_link

    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = session.get(MigratedGroupRecovery, int(row_id))
        if row is None:
            return status
        row.readd_status = status
        row.readd_result = payload
        row.invite_link = invite_link
        row.last_error = last_error
        row.readd_completed_at = now
        row.updated_at = now
        session.commit()
    return status


async def _process_row(row: RecoveryRow) -> str:
    from bot.services.mtproto_dm_gc_listener import get_listener_client

    cfg = get_club_gc_config_by_link_club_id(int(row.club_id))

    async def _finish(
        result: ReaddGroupResult,
        *,
        pre_status: str | None = None,
        pre_error: str | None = None,
    ) -> str:
        status = finalize_row(
            row.id,
            result,
            pre_status=pre_status,
            pre_error=pre_error,
        )
        if cfg is not None:
            try:
                await notify_readd_admin_dm(
                    cfg,
                    row=row,
                    result=result,
                    terminal_status=status,
                )
            except Exception:
                logger.warning(
                    "migration_recovery: admin DM failed row_id=%s chat_id=%s",
                    row.id,
                    row.telegram_chat_id,
                    exc_info=True,
                )
            await _notify_rt_ops_if_needed(
                row=row,
                result=result,
                terminal_status=status,
                pre_error=pre_error,
            )
        elif pre_error or status == "failed":
            await _notify_rt_ops_if_needed(
                row=row,
                result=result,
                terminal_status=status,
                pre_error=pre_error,
            )
        return status

    if cfg is None:
        return await _finish(
            ReaddGroupResult(
                chat_id=row.telegram_chat_id,
                club_id=row.club_id,
                club_key=row.club_key,
                title=row.group_title,
                member_count_before=0,
                member_count_after=None,
                status="error",
                error="no_mtproto_config",
            ),
        )

    client = get_listener_client(cfg.club_key)
    if client is None or not client.is_connected():
        return await _finish(
            ReaddGroupResult(
                chat_id=row.telegram_chat_id,
                club_id=row.club_id,
                club_key=row.club_key,
                title=row.group_title,
                member_count_before=0,
                member_count_after=None,
                status="error",
            ),
            pre_status="failed",
            pre_error="listener_not_connected",
        )

    listener_user_id: int | None = None
    try:
        me = await client.get_me()
        if me and getattr(me, "id", None):
            listener_user_id = int(me.id)
    except Exception:
        logger.warning("migration_recovery: get_me failed club_key=%s", cfg.club_key)

    group = LinkedGroupRow(
        chat_id=int(row.telegram_chat_id),
        club_id=int(row.club_id),
        title=row.group_title,
    )
    result = await readd_group(
        client=client,
        cfg=cfg,
        group=group,
        dialog_chat_id=int(row.telegram_chat_id),
        player_id=row.player_telegram_user_id,
        player_username=row.player_username,
        apply=True,
        update_invite_links=True,
        invite_staff=True,
        listener_user_id=listener_user_id,
    )
    return await _finish(result)


async def tick_async() -> dict[str, int]:
    if not is_migration_recovery_enabled():
        return {"claimed": 0}

    rows = claim_pending_batch()
    if not rows:
        logger.info("migration_recovery: no pending rows")
        return {"claimed": 0}

    summary = {
        "claimed": len(rows),
        "complete": 0,
        "privacy_blocked": 0,
        "failed": 0,
        "skipped": 0,
    }
    delay = get_migration_recovery_invite_delay_sec()
    current_row: RecoveryRow | None = None

    async def _on_flood_wait(label: str, wait_s: int) -> None:
        detail = f"Telegram rate limit (FloodWait): waiting {wait_s}s during {label}"
        await notify_rt_ops_issue(
            issue_kind="rate_limit",
            detail=detail,
            row=current_row,
        )

    set_flood_wait_observer(_on_flood_wait)
    try:
        for i, row in enumerate(rows):
            if i > 0 and delay > 0:
                await asyncio.sleep(delay)
            current_row = row
            try:
                status = await _process_row(row)
            except Exception:
                logger.exception(
                    "migration_recovery: tick failed row_id=%s chat_id=%s",
                    row.id,
                    row.telegram_chat_id,
                )
                err_result = ReaddGroupResult(
                    chat_id=row.telegram_chat_id,
                    club_id=row.club_id,
                    club_key=row.club_key,
                    title=row.group_title,
                    member_count_before=0,
                    member_count_after=None,
                    status="error",
                    error="tick_exception",
                )
                status = finalize_row(row.id, err_result)
                cfg = get_club_gc_config_by_link_club_id(int(row.club_id))
                if cfg is not None:
                    try:
                        await notify_readd_admin_dm(
                            cfg,
                            row=row,
                            result=err_result,
                            terminal_status=status,
                        )
                    except Exception:
                        logger.warning(
                            "migration_recovery: admin DM failed row_id=%s chat_id=%s",
                            row.id,
                            row.telegram_chat_id,
                            exc_info=True,
                        )
                await _notify_rt_ops_if_needed(
                    row=row,
                    result=err_result,
                    terminal_status=status,
                    pre_error="tick_exception",
                )
            if status in summary:
                summary[status] += 1
            else:
                summary["failed"] += 1
            current_row = None
    finally:
        set_flood_wait_observer(None)

    logger.info(
        "migration_recovery tick: claimed=%s complete=%s privacy=%s failed=%s skipped=%s",
        summary["claimed"],
        summary["complete"],
        summary["privacy_blocked"],
        summary["failed"],
        summary["skipped"],
    )
    return summary


def schedule_migration_recovery_tick() -> None:
    from bot.services.mtproto_dm_gc_listener import _loop_holder

    loop = _loop_holder.get("loop")
    if loop is None or not loop.is_running():
        logger.warning("migration_recovery: listener loop not running; skipping tick")
        try:
            main_loop = asyncio.get_running_loop()
        except RuntimeError:
            main_loop = None
        if main_loop is not None:
            main_loop.create_task(
                notify_rt_ops_issue(
                    issue_kind="listener_not_ready",
                    detail=(
                        "Migration recovery tick skipped: dm_gc Telethon listener "
                        "loop is not running."
                    ),
                ),
                name="migration-recovery-listener-down",
            )
        return
    asyncio.run_coroutine_threadsafe(tick_async(), loop)


def migration_recovery_job_callback(context) -> None:
    schedule_migration_recovery_tick()


def schedule_migration_recovery_job(app) -> None:
    from datetime import timedelta

    from club_gc_settings import get_migration_recovery_interval_sec

    interval_sec = get_migration_recovery_interval_sec()
    app.job_queue.run_repeating(
        migration_recovery_job_callback,
        interval=timedelta(seconds=interval_sec),
        first=timedelta(seconds=60),
        name="migration_recovery",
    )
    logger.info(
        "migration_recovery job scheduled interval_sec=%s batch_size=%s",
        interval_sec,
        get_migration_recovery_batch_size(),
    )


def recovery_status_counts() -> dict[str, dict[str, int]]:
    from sqlalchemy import func

    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    with get_db() as session:
        by_status = dict(
            session.query(MigratedGroupRecovery.readd_status, func.count())
            .group_by(MigratedGroupRecovery.readd_status)
            .all()
        )
        by_tier = dict(
            session.query(MigratedGroupRecovery.priority_tier, func.count())
            .group_by(MigratedGroupRecovery.priority_tier)
            .all()
        )
    return {
        "by_status": {str(k): int(v) for k, v in by_status.items()},
        "by_tier": {str(k): int(v) for k, v in by_tier.items()},
    }
