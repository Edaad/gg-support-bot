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
from bot.services.migration_group_readd import ReaddGroupResult, readd_group
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


def classify_priority_tier(
    *,
    deposit_cents: int,
    active_in_past_30_days: bool,
) -> int:
    """Return priority tier: 1=deposits, 2=active, 3=rest."""
    if int(deposit_cents) > 0:
        return 1
    if active_in_past_30_days:
        return 2
    return 3


def compute_priority_rank(
    *,
    priority_tier: int,
    deposit_cents: int,
    last_activity_epoch: int,
    telegram_chat_id: int,
    sequence: int,
) -> int:
    """Lower rank = higher priority within global ordering (tier ASC, rank ASC)."""
    tier_base = int(priority_tier) * 10_000_000_000
    if priority_tier == 1:
        deposit_key = min(int(deposit_cents), 9_999_999_999)
        return tier_base + (9_999_999_999 - deposit_key) * 10 + int(sequence)
    if priority_tier == 2:
        activity_key = min(max(int(last_activity_epoch), 0), 9_999_999_999)
        return tier_base + (9_999_999_999 - activity_key) * 10 + int(sequence)
    return tier_base + abs(int(telegram_chat_id)) % 9_999_999_999 + int(sequence)


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
    if cfg is None:
        finalize_row(
            row.id,
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
        return "failed"

    client = get_listener_client(cfg.club_key)
    if client is None or not client.is_connected():
        finalize_row(
            row.id,
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
        return "failed"

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
    return finalize_row(row.id, result)


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

    for i, row in enumerate(rows):
        if i > 0 and delay > 0:
            await asyncio.sleep(delay)
        try:
            status = await _process_row(row)
        except Exception:
            logger.exception(
                "migration_recovery: tick failed row_id=%s chat_id=%s",
                row.id,
                row.telegram_chat_id,
            )
            finalize_row(
                row.id,
                ReaddGroupResult(
                    chat_id=row.telegram_chat_id,
                    club_id=row.club_id,
                    club_key=row.club_key,
                    title=row.group_title,
                    member_count_before=0,
                    member_count_after=None,
                    status="error",
                    error="tick_exception",
                ),
            )
            status = "failed"
        if status in summary:
            summary[status] += 1
        else:
            summary["failed"] += 1

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
