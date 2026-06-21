"""Worker cron: batch direct-add for migrated supergroup recovery queue."""

from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from club_gc_settings import (
    MIGRATION_RECOVERY_CLUB_KEYS,
    get_club_gc_config_by_link_club_id,
    get_migration_recovery_batch_size,
    get_migration_recovery_disabled_clubs,
    get_migration_recovery_interval_sec,
    get_migration_recovery_invite_delay_sec,
    is_migration_recovery_enabled,
    is_round_table_elevate_recovery_enabled,
    migration_recovery_active_club_keys,
)
from bot.services.migration_group_readd import (
    ElevateJoinResult,
    FloodWaitAbortError,
    ReaddGroupResult,
    elevate_join_recovery_group,
    get_flood_wait_policy,
    readd_group,
    readd_round_table_player_and_link,
    set_flood_wait_observer,
    set_flood_wait_policy,
)
from scripts.backfill_support_group_invite_links import LinkedGroupRow

logger = logging.getLogger(__name__)

RECOVERY_CLUB_KEYS = MIGRATION_RECOVERY_CLUB_KEYS

TERMINAL_STATUSES = frozenset(
    {"complete", "privacy_blocked", "failed", "skipped"}
)

HIGH_PRIORITY_TIERS = (1, 2)
LOW_PRIORITY_TIERS = (3,)

# Per-club tier scope: RT finishes deposits + active (1+2); CC/GTO run tier 3.
CLUB_RECOVERY_PRIORITY_TIERS: dict[str, tuple[int, ...]] = {
    "round_table": HIGH_PRIORITY_TIERS,
    "creator_club": LOW_PRIORITY_TIERS,
    "clubgto": LOW_PRIORITY_TIERS,
}


def recovery_priority_tiers_for_club(club_key: str) -> tuple[int, ...]:
    return CLUB_RECOVERY_PRIORITY_TIERS.get(club_key, HIGH_PRIORITY_TIERS)

_FLOOD_WAIT_ABORT_RE = re.compile(
    r"FloodWait (\d+)s during ([^\s]+(?:\:\d+)?)",
    re.IGNORECASE,
)

_MIGRATION_RECOVERY_MIN_BOOT_DELAY_SEC = 60.0
_MIGRATION_RECOVERY_SLACK_SUMMARY_MIN_BOOT_DELAY_SEC = 60.0


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


@dataclass(frozen=True)
class ClubRecoveryQueueSnapshot:
    """Per-club queue counts for Slack (all tiers)."""

    club_key: str
    club_display_name: str
    tier12_pending: int
    tier3_pending: int
    skipped: int
    failed: int
    processing: int


@dataclass(frozen=True)
class ClubRecoverySlackStats:
    club_key: str
    club_display_name: str
    total: int
    left: int
    done: int
    pct_done: float
    in_group: int
    pct_in_group: float
    in_group_pending: int
    check_errors: int
    direct_added: int
    invite_link: int
    still_missing: int


@dataclass(frozen=True)
class _RecoverySlackScanRow:
    """Detached snapshot for Slack stats (ORM rows expire after session close)."""

    id: int
    club_key: str
    readd_status: str
    telegram_chat_id: int
    readd_result: dict[str, Any] | None


def was_direct_added(readd_result: dict[str, Any] | None) -> bool:
    added = (readd_result or {}).get("added") or []
    return any(str(x).startswith("player:") for x in added)


def is_already_in_only_result(result: ReaddGroupResult) -> bool:
    return (
        bool(result.already_member)
        and not result.added
        and not result.privacy_blocked
        and not result.failed
        and result.status not in ("no_targets", "error")
    )


def consumes_direct_add_quota(result: ReaddGroupResult) -> bool:
    if result.status == "no_targets":
        return False
    if is_already_in_only_result(result):
        return False
    return True


def should_skip_admin_dm_for_result(result: ReaddGroupResult, terminal_status: str) -> bool:
    return terminal_status == "complete" and is_already_in_only_result(result)


def classify_terminal_row_outcome(
    *,
    readd_result: dict[str, Any] | None,
    player_in_group: bool,
) -> str:
    """Return ``direct_added``, ``invite_link``, or ``still_missing``."""
    if not player_in_group:
        return "still_missing"
    if was_direct_added(readd_result):
        return "direct_added"
    return "invite_link"


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


def flood_wait_abort_from_readd_result(result: ReaddGroupResult) -> FloodWaitAbortError | None:
    """Parse a swallowed FloodWait abort from readd_group failure blobs."""

    for blob in (result.error, "; ".join(result.failed)):
        if not blob:
            continue
        match = _FLOOD_WAIT_ABORT_RE.search(blob)
        if match is not None:
            return FloodWaitAbortError(int(match.group(1)), match.group(2))
    return None


def build_readd_result_payload(result: ReaddGroupResult) -> dict[str, Any]:
    payload = {
        "inner_status": result.status,
        "added": list(result.added),
        "already_member": list(result.already_member),
        "privacy_blocked": list(result.privacy_blocked),
        "failed": list(result.failed),
        "member_count_before": result.member_count_before,
        "member_count_after": result.member_count_after,
        "error": result.error,
    }
    if result.resolved_player_id is not None:
        payload["resolved_player_id"] = int(result.resolved_player_id)
        payload["resolved_player_username"] = result.resolved_player_username
        payload["resolved_player_display_name"] = result.resolved_player_display_name
        payload["resolved_player_source"] = result.resolved_player_source
    return payload


def elevate_joined_in_payload(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    return bool(payload.get("elevate_joined"))


def merge_elevate_into_payload(
    payload: dict[str, Any],
    elevate: ElevateJoinResult,
) -> dict[str, Any]:
    merged = dict(payload)
    merged["elevate_joined"] = elevate.joined or elevate.already_member
    merged["elevate_join_error"] = elevate.error
    if elevate.joined or elevate.already_member:
        merged["elevate_join_at"] = datetime.now(timezone.utc).isoformat()
    if elevate.already_member:
        merged["elevate_already_member"] = True
    return merged


def _player_readd_ok(result: ReaddGroupResult) -> bool:
    status, _err = map_readd_status(result)
    if status == "complete":
        return True
    return bool(result.added or result.already_member) and not result.failed


def map_readd_status_with_elevate(
    result: ReaddGroupResult,
    *,
    elevate: ElevateJoinResult | None,
    require_elevate: bool,
) -> tuple[str, str | None]:
    base_status, base_error = map_readd_status(result)
    if not require_elevate:
        return base_status, base_error
    if not _player_readd_ok(result):
        return base_status, base_error
    if elevate is None:
        return "processing", None
    if elevate.dry_run:
        return base_status, base_error
    if elevate.joined or elevate.already_member:
        return "complete", None
    if elevate.error:
        return "failed", f"elevate_join:{elevate.error}"
    return "processing", None


def count_elevate_pending_rows() -> int:
    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    tiers = recovery_priority_tiers_for_club("round_table")
    count = 0
    with get_db() as session:
        rows = (
            session.query(MigratedGroupRecovery)
            .filter(MigratedGroupRecovery.club_key == "round_table")
            .filter(MigratedGroupRecovery.priority_tier.in_(tiers))
            .filter(MigratedGroupRecovery.invite_link.isnot(None))
            .filter(MigratedGroupRecovery.invite_link != "")
            .all()
        )
        for row in rows:
            payload = row.readd_result if isinstance(row.readd_result, dict) else {}
            if not elevate_joined_in_payload(payload):
                count += 1
    return count


def find_oldest_elevate_pending_row() -> RecoveryRow | None:
    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    tiers = recovery_priority_tiers_for_club("round_table")
    with get_db() as session:
        rows = (
            session.query(MigratedGroupRecovery)
            .filter(MigratedGroupRecovery.club_key == "round_table")
            .filter(MigratedGroupRecovery.priority_tier.in_(tiers))
            .filter(MigratedGroupRecovery.invite_link.isnot(None))
            .filter(MigratedGroupRecovery.invite_link != "")
            .order_by(
                MigratedGroupRecovery.priority_tier.asc(),
                MigratedGroupRecovery.priority_rank.asc(),
            )
            .all()
        )
        for row in rows:
            payload = row.readd_result if isinstance(row.readd_result, dict) else {}
            if elevate_joined_in_payload(payload):
                continue
            return _recovery_row_from_model(row)
    return None


def _load_row_invite_link(row_id: int) -> str | None:
    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    with get_db() as session:
        row = session.get(MigratedGroupRecovery, int(row_id))
        if row is None:
            return None
        link = (row.invite_link or "").strip()
        return link or None


def should_persist_resolved_player(
    result: ReaddGroupResult,
    *,
    stored_player_id: int | None,
) -> bool:
    """True when re-add resolved a different player id and invite succeeded."""
    if result.resolved_player_id is None:
        return False
    if stored_player_id is not None and int(result.resolved_player_id) == int(stored_player_id):
        return False
    return bool(result.added or result.already_member)


def persist_resolved_recovery_player(
    *,
    row_id: int,
    club_key: str,
    club_display_name: str,
    telegram_chat_id: int,
    group_title: str,
    player_id: int,
    player_username: str | None,
    player_display_name: str | None,
) -> bool:
    """Update migrated_group_recovery + support_group_chats with a resolved player id."""
    from bot.services.support_group_chats import bind_player_for_gc_reuse
    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    pid = int(player_id)
    un = (player_username or "").strip() or None
    dn = (player_display_name or "").strip() or None
    recovery_changed = False
    with get_db() as session:
        row = session.get(MigratedGroupRecovery, int(row_id))
        if row is None:
            return False
        if row.player_telegram_user_id != pid:
            row.player_telegram_user_id = pid
            recovery_changed = True
        if un is not None and (row.player_username or "") != un:
            row.player_username = un
            recovery_changed = True
        if dn is not None and (row.player_display_name or "") != dn:
            row.player_display_name = dn
            recovery_changed = True

    bind_status, _bind_row_id = bind_player_for_gc_reuse(
        club_key=club_key,
        club_display_name=club_display_name,
        telegram_chat_id=int(telegram_chat_id),
        telegram_chat_title=group_title,
        player_telegram_user_id=pid,
        player_username=un,
        player_display_name=dn,
    )
    return recovery_changed or bind_status in ("updated", "inserted")


def maybe_persist_resolved_player_from_readd(
    row: RecoveryRow,
    result: ReaddGroupResult,
    cfg,
) -> bool:
    if not should_persist_resolved_player(
        result,
        stored_player_id=row.player_telegram_user_id,
    ):
        return False
    changed = persist_resolved_recovery_player(
        row_id=row.id,
        club_key=cfg.club_key,
        club_display_name=cfg.club_display_name,
        telegram_chat_id=int(row.telegram_chat_id),
        group_title=row.group_title,
        player_id=int(result.resolved_player_id),
        player_username=result.resolved_player_username,
        player_display_name=result.resolved_player_display_name,
    )
    if changed:
        logger.info(
            "migration_recovery: persisted resolved player row_id=%s chat_id=%s "
            "player_id=%s source=%s",
            row.id,
            row.telegram_chat_id,
            result.resolved_player_id,
            result.resolved_player_source,
        )
    return changed


def build_membership_audit_readd_result(
    *,
    eligible_player_ids: tuple[int, ...],
) -> ReaddGroupResult:
    """Synthetic re-add result when MTProto audit finds player(s) already present."""
    markers = [f"player:{pid}" for pid in eligible_player_ids]
    return ReaddGroupResult(
        chat_id=0,
        club_id=0,
        club_key="",
        title="",
        member_count_before=0,
        member_count_after=None,
        status="ok",
        already_member=markers,
    )


def maybe_finalize_recovery_row_from_membership(
    row_id: int,
    *,
    eligible_player_ids: tuple[int, ...],
) -> bool:
    """Mark a pending/processing row complete when MTProto confirms player presence.

    Returns True when the row was finalized.
    """
    if not eligible_player_ids:
        return False

    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    with get_db() as session:
        row = session.get(MigratedGroupRecovery, int(row_id))
        if row is None:
            return False
        if row.readd_status not in ("pending", "processing"):
            return False
        snapshot = _recovery_row_from_model(row)

    template = build_membership_audit_readd_result(
        eligible_player_ids=eligible_player_ids,
    )
    result = ReaddGroupResult(
        chat_id=int(snapshot.telegram_chat_id),
        club_id=int(snapshot.club_id),
        club_key=str(snapshot.club_key),
        title=str(snapshot.group_title),
        member_count_before=template.member_count_before,
        member_count_after=template.member_count_after,
        status=template.status,
        already_member=list(template.already_member),
    )
    payload = build_readd_result_payload(result)
    payload["membership_audit"] = True

    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = session.get(MigratedGroupRecovery, int(row_id))
        if row is None or row.readd_status not in ("pending", "processing"):
            return False
        row.readd_status = "complete"
        row.readd_result = payload
        row.readd_completed_at = now
        row.updated_at = now
        session.commit()
    return True


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


def is_readd_success_with_add(result: ReaddGroupResult, terminal_status: str) -> bool:
    """True when the player was direct-added and the row finalized as complete."""
    return terminal_status == "complete" and bool(result.added)


def _player_username_for_notification(
    row: RecoveryRow,
    result: ReaddGroupResult,
) -> str:
    if result.resolved_player_username:
        username = result.resolved_player_username.strip().lstrip("@")
        if username:
            return f"@{username}"
    added = _format_account_lines(result.added)
    if added:
        marker = added[0].strip()
        if marker and not marker.isdigit():
            return marker if marker.startswith("@") else f"@{marker.lstrip('@')}"
        if marker:
            return marker
    if row.player_username:
        username = row.player_username.strip().lstrip("@")
        if username:
            return f"@{username}"
    if row.player_telegram_user_id is not None:
        return str(row.player_telegram_user_id)
    return "player"


def format_readd_success_admin_notification(
    *,
    row: RecoveryRow,
    result: ReaddGroupResult,
) -> str:
    """Short HTML DM when a player was direct-added back to their GC."""
    username = _player_username_for_notification(row, result)
    gc_name = (row.group_title or result.title or "").strip() or "group chat"
    safe_user = html.escape(username, quote=False)
    safe_gc = html.escape(gc_name, quote=False)
    return f"{safe_user} successfully added back to {safe_gc}"


def _readd_error_blobs(
    result: ReaddGroupResult,
    *,
    pre_error: str | None = None,
) -> list[str]:
    blobs: list[str] = []
    if pre_error:
        blobs.append(pre_error)
    if result.error:
        blobs.append(result.error)
    blobs.extend(result.failed)
    return blobs


def deactivated_account_hint(
    result: ReaddGroupResult,
    *,
    pre_error: str | None = None,
) -> str | None:
    for blob in _readd_error_blobs(result, pre_error=pre_error):
        if "entity_resolution_failed" in blob.lower():
            return "The Telegram account may have been deactivated."
        if "ValueError" in blob:
            return "The Telegram account may have been deactivated."
        if "could not find the input entity" in blob.lower():
            return "The Telegram account may have been deactivated."
        if "no user has" in blob.lower() and "as username" in blob.lower():
            return "The Telegram account may have been deactivated."
        if "username is not in use" in blob.lower():
            return "The Telegram account may have been deactivated."
    return None


async def build_readd_admin_notification(
    *,
    row: RecoveryRow,
    result: ReaddGroupResult,
    terminal_status: str,
    club_display_name: str,
) -> str:
    del club_display_name, terminal_status
    return format_readd_success_admin_notification(row=row, result=result)


async def notify_readd_admin_dm(
    cfg,
    *,
    row: RecoveryRow,
    result: ReaddGroupResult,
    terminal_status: str,
) -> None:
    from bot.services.mtproto_track_contact import notify_club_gc_admin_dm

    if not is_readd_success_with_add(result, terminal_status):
        return
    text = await build_readd_admin_notification(
        row=row,
        result=result,
        terminal_status=terminal_status,
        club_display_name=cfg.club_display_name,
    )
    await notify_club_gc_admin_dm(cfg, text, parse_mode="HTML")


def should_notify_rt_ops(
    terminal_status: str,
    result: ReaddGroupResult,
    *,
    pre_error: str | None = None,
) -> bool:
    if terminal_status in ("failed", "privacy_blocked"):
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
    from bot.services.slack_ops_notify import notify_slack_ops

    await notify_slack_ops(text, source="migration_recovery")


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
    if result.privacy_blocked:
        privacy = _format_account_lines(result.privacy_blocked)
        detail = f"{detail}\nPrivacy blocked: {'; '.join(privacy)}"
    hint = deactivated_account_hint(result, pre_error=pre_error)
    if hint:
        detail = f"{detail}\n{hint}"
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


_recovery_app: Any | None = None


def _recovery_row_from_model(row) -> RecoveryRow:
    return RecoveryRow(
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


def _recovery_slack_scan_row_from_model(row) -> _RecoverySlackScanRow:
    return _RecoverySlackScanRow(
        id=int(row.id),
        club_key=str(row.club_key),
        readd_status=str(row.readd_status),
        telegram_chat_id=int(row.telegram_chat_id),
        readd_result=row.readd_result if isinstance(row.readd_result, dict) else None,
    )


def pending_count_by_club(*, include_processing: bool = False) -> dict[str, int]:
    """Count scoped queue rows per club (pending, optionally including processing)."""

    from sqlalchemy import func

    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    statuses = ["pending"]
    if include_processing:
        statuses.append("processing")

    counts: dict[str, int] = {}
    with get_db() as session:
        for club_key in RECOVERY_CLUB_KEYS:
            tiers = recovery_priority_tiers_for_club(club_key)
            count = (
                session.query(func.count())
                .filter(MigratedGroupRecovery.club_key == club_key)
                .filter(MigratedGroupRecovery.readd_status.in_(statuses))
                .filter(MigratedGroupRecovery.priority_tier.in_(tiers))
                .scalar()
            )
            counts[str(club_key)] = int(count or 0)
    return counts


def is_migration_recovery_auto_disabled() -> bool:
    try:
        from db.connection import get_db
        from db.models import MigrationRecoveryControl

        with get_db() as session:
            row = session.get(MigrationRecoveryControl, 1)
            return row is not None and row.auto_disabled_at is not None
    except Exception:
        return False


def get_rate_limit_resume_at() -> datetime | None:
    try:
        from db.connection import get_db
        from db.models import MigrationRecoveryControl

        with get_db() as session:
            row = session.get(MigrationRecoveryControl, 1)
            if row is None or row.rate_limit_resume_at is None:
                return None
            value = row.rate_limit_resume_at
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
    except Exception:
        logger.warning(
            "migration_recovery: failed to read rate_limit_resume_at",
            exc_info=True,
        )
        return None


def is_rate_limit_pause_pending(*, now: datetime | None = None) -> bool:
    try:
        from db.connection import get_db
        from db.models import MigrationRecoveryControl

        with get_db() as session:
            row = session.get(MigrationRecoveryControl, 1)
            if row is None or row.auto_disabled_at is None:
                return False
            if row.auto_disabled_reason != "rate_limit":
                return False
            resume_at = row.rate_limit_resume_at
            if resume_at is None:
                return False
            if resume_at.tzinfo is None:
                resume_at = resume_at.replace(tzinfo=timezone.utc)
            current = now or datetime.now(timezone.utc)
            return current < resume_at
    except Exception:
        return False


def compute_rate_limit_resume_at(
    *,
    flood_wait_sec: int,
    now: datetime | None = None,
) -> datetime:
    from club_gc_settings import get_migration_recovery_rate_limit_cooldown_sec

    current = now or datetime.now(timezone.utc)
    return current + timedelta(
        seconds=int(flood_wait_sec) + get_migration_recovery_rate_limit_cooldown_sec()
    )


def maybe_apply_rate_limit_resume(*, now: datetime | None = None) -> bool:
    """Clear a rate-limit pause once ``rate_limit_resume_at`` has passed."""

    from db.connection import get_db
    from db.models import MigrationRecoveryControl

    current = now or datetime.now(timezone.utc)
    with get_db() as session:
        row = session.get(MigrationRecoveryControl, 1)
        if row is None or row.auto_disabled_at is None:
            return False
        if row.auto_disabled_reason != "rate_limit":
            return False
        resume_at = row.rate_limit_resume_at
        if resume_at is None:
            return False
        if resume_at.tzinfo is None:
            resume_at = resume_at.replace(tzinfo=timezone.utc)
        if current < resume_at:
            return False
        row.auto_disabled_at = None
        row.auto_disabled_reason = None
        row.exhausted_club_key = None
        row.pending_snapshot = None
        row.rate_limit_resume_at = None
        session.commit()
    return True


def compute_rate_limit_resume_delay_sec(*, now: datetime | None = None) -> float | None:
    resume_at = get_rate_limit_resume_at()
    if resume_at is None:
        return None
    current = now or datetime.now(timezone.utc)
    remaining = (resume_at - current).total_seconds()
    if remaining <= 0:
        return _MIGRATION_RECOVERY_MIN_BOOT_DELAY_SEC
    return remaining


def get_last_tick_at() -> datetime | None:
    try:
        from db.connection import get_db
        from db.models import MigrationRecoveryControl

        with get_db() as session:
            row = session.get(MigrationRecoveryControl, 1)
            if row is None or row.last_tick_at is None:
                return None
            value = row.last_tick_at
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
    except Exception:
        logger.warning("migration_recovery: failed to read last_tick_at", exc_info=True)
        return None


def record_migration_recovery_tick() -> None:
    from db.connection import get_db
    from db.models import MigrationRecoveryControl

    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = session.get(MigrationRecoveryControl, 1)
        if row is None:
            row = MigrationRecoveryControl(id=1)
            session.add(row)
        row.last_tick_at = now
        session.commit()


def get_last_slack_summary_at() -> datetime | None:
    from db.connection import get_db
    from db.models import MigrationRecoveryControl

    try:
        with get_db() as session:
            row = session.get(MigrationRecoveryControl, 1)
            if row is None:
                return None
            value = row.last_slack_summary_at
            if value is None:
                return None
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
    except Exception:
        logger.warning(
            "migration_recovery: failed to read last_slack_summary_at",
            exc_info=True,
        )
        return None


def record_slack_summary_post() -> None:
    from db.connection import get_db
    from db.models import MigrationRecoveryControl

    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = session.get(MigrationRecoveryControl, 1)
        if row is None:
            row = MigrationRecoveryControl(id=1)
            session.add(row)
        row.last_slack_summary_at = now
        session.commit()


def compute_migration_recovery_slack_summary_first_delay_sec(
    *,
    now: datetime | None = None,
) -> float:
    from club_gc_settings import get_migration_recovery_slack_summary_interval_sec

    interval_sec = get_migration_recovery_slack_summary_interval_sec()
    last = get_last_slack_summary_at()
    if last is None:
        return float(interval_sec)

    current = now or datetime.now(timezone.utc)
    remaining = (last + timedelta(seconds=interval_sec) - current).total_seconds()
    if remaining <= 0:
        return _MIGRATION_RECOVERY_SLACK_SUMMARY_MIN_BOOT_DELAY_SEC
    return remaining


def compute_migration_recovery_first_delay_sec(
    *,
    now: datetime | None = None,
) -> float:
    interval_sec = get_migration_recovery_interval_sec()
    last = get_last_tick_at()
    if last is None:
        return _MIGRATION_RECOVERY_MIN_BOOT_DELAY_SEC

    current = now or datetime.now(timezone.utc)
    remaining = (last + timedelta(seconds=interval_sec) - current).total_seconds()
    if remaining <= 0:
        return _MIGRATION_RECOVERY_MIN_BOOT_DELAY_SEC
    return remaining


def clear_migration_recovery_auto_disable() -> bool:
    """Clear DB auto-disable flag. Returns True if a flag was cleared."""

    from db.connection import get_db
    from db.models import MigrationRecoveryControl

    with get_db() as session:
        row = session.get(MigrationRecoveryControl, 1)
        if row is None or row.auto_disabled_at is None:
            return False
        row.auto_disabled_at = None
        row.auto_disabled_reason = None
        row.exhausted_club_key = None
        row.pending_snapshot = None
        row.rate_limit_resume_at = None
        session.commit()
    return True


def format_auto_disable_notification(
    *,
    reason: str,
    exhausted_club_key: str,
    pending_snapshot: dict[str, int],
) -> str:
    club_lines = [
        f"  {club_key}: {pending_snapshot.get(club_key, 0)} in queue"
        for club_key in RECOVERY_CLUB_KEYS
    ]
    lines = [
        "Migration recovery auto-disabled.",
        f"Reason: {reason}",
        f"Exhausted club: {exhausted_club_key}",
        "Queue counts (pending + processing):",
        *club_lines,
    ]
    if reason == "club_exhausted":
        lines.append(
            "One club finished before others. Review remaining queues, then clear "
            "the DB flag and re-enable if you want to continue other clubs."
        )
    elif reason == "rate_limit":
        resume_at = get_rate_limit_resume_at()
        if resume_at is not None:
            lines.append(
                f"Auto-resume scheduled at {resume_at.isoformat()} "
                "(1h after FloodWait ends; persisted in DB)."
            )
        else:
            lines.append(
                "Telegram rate limit hit. Clear the DB flag and re-enable after the wait."
            )
    else:
        lines.append("All clubs drained. Recovery complete.")
    lines.append(
        "Operator: heroku config:unset GC_MIGRATION_RECOVERY_ENABLED -a YOUR_APP"
    )
    return "\n".join(lines)


def remove_migration_recovery_job() -> None:
    global _recovery_app

    if _recovery_app is None:
        return
    job_queue = getattr(_recovery_app, "job_queue", None)
    if job_queue is None:
        return
    for job in job_queue.get_jobs_by_name("migration_recovery"):
        job.schedule_removal()


def remove_migration_recovery_rate_limit_resume_job() -> None:
    global _recovery_app

    if _recovery_app is None:
        return
    job_queue = getattr(_recovery_app, "job_queue", None)
    if job_queue is None:
        return
    for job in job_queue.get_jobs_by_name("migration_recovery_rate_limit_resume"):
        job.schedule_removal()


def persist_auto_disable_flag(
    *,
    reason: str,
    exhausted_club_key: str,
    pending_snapshot: dict[str, int],
    rate_limit_resume_at: datetime | None = None,
) -> None:
    from db.connection import get_db
    from db.models import MigrationRecoveryControl

    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = session.get(MigrationRecoveryControl, 1)
        if row is None:
            row = MigrationRecoveryControl(id=1)
            session.add(row)
        row.auto_disabled_at = now
        row.auto_disabled_reason = reason
        row.exhausted_club_key = exhausted_club_key
        row.pending_snapshot = dict(pending_snapshot)
        if reason == "rate_limit":
            row.rate_limit_resume_at = rate_limit_resume_at
        else:
            row.rate_limit_resume_at = None
        session.commit()


def extend_rate_limit_pause(*, flood_wait_sec: int) -> bool:
    """Extend an active rate-limit pause if the new resume time is later."""

    from db.connection import get_db
    from db.models import MigrationRecoveryControl

    resume_at = compute_rate_limit_resume_at(flood_wait_sec=flood_wait_sec)
    with get_db() as session:
        row = session.get(MigrationRecoveryControl, 1)
        if row is None or row.auto_disabled_at is None:
            return False
        if row.auto_disabled_reason != "rate_limit":
            return False
        existing = row.rate_limit_resume_at
        if existing is not None and existing.tzinfo is None:
            existing = existing.replace(tzinfo=timezone.utc)
        if existing is not None and resume_at <= existing:
            return False
        row.rate_limit_resume_at = resume_at
        session.commit()
    return True


async def auto_disable_migration_recovery(
    *,
    reason: str,
    exhausted_club_key: str,
    pending_snapshot: dict[str, int],
    flood_wait_sec: int | None = None,
) -> None:
    if is_migration_recovery_auto_disabled():
        if reason == "rate_limit" and flood_wait_sec is not None:
            updated = extend_rate_limit_pause(flood_wait_sec=flood_wait_sec)
            if updated and _recovery_app is not None:
                schedule_migration_recovery_rate_limit_resume_job(_recovery_app)
        return

    resume_at = None
    if reason == "rate_limit" and flood_wait_sec is not None:
        resume_at = compute_rate_limit_resume_at(flood_wait_sec=flood_wait_sec)

    persist_auto_disable_flag(
        reason=reason,
        exhausted_club_key=exhausted_club_key,
        pending_snapshot=pending_snapshot,
        rate_limit_resume_at=resume_at,
    )
    remove_migration_recovery_job()

    text = format_auto_disable_notification(
        reason=reason,
        exhausted_club_key=exhausted_club_key,
        pending_snapshot=pending_snapshot,
    )
    try:
        await notify_rt_ops_issue(issue_kind=reason, detail=text)
    except Exception:
        logger.warning(
            "migration_recovery: auto-disable RT ops DM failed club=%s",
            exhausted_club_key,
            exc_info=True,
        )

    logger.info(
        "migration_recovery auto-disabled reason=%s exhausted_club=%s snapshot=%s "
        "rate_limit_resume_at=%s",
        reason,
        exhausted_club_key,
        pending_snapshot,
        resume_at.isoformat() if resume_at is not None else None,
    )

    if reason == "rate_limit" and resume_at is not None and _recovery_app is not None:
        schedule_migration_recovery_rate_limit_resume_job(_recovery_app)


async def _maybe_auto_disable_after_tick() -> None:
    active_clubs = migration_recovery_active_club_keys()
    if not active_clubs:
        return

    counts = pending_count_by_club(include_processing=True)
    exhausted = [k for k in active_clubs if counts.get(k, 0) == 0]
    if len(exhausted) < len(active_clubs):
        return

    await auto_disable_migration_recovery(
        reason="all_clubs_drained",
        exhausted_club_key=exhausted[0],
        pending_snapshot=counts,
    )


def claim_pending_batch(limit: int | None = None) -> list[RecoveryRow]:
    from sqlalchemy import select

    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    batch_size = int(limit or get_migration_recovery_batch_size())
    now = datetime.now(timezone.utc)
    all_ids: list[int] = []

    active_clubs = migration_recovery_active_club_keys()
    if not active_clubs:
        return []

    with get_db() as session:
        for club_key in active_clubs:
            tiers = recovery_priority_tiers_for_club(club_key)
            ids = list(
                session.execute(
                    select(MigratedGroupRecovery.id)
                    .where(MigratedGroupRecovery.readd_status == "pending")
                    .where(MigratedGroupRecovery.club_key == club_key)
                    .where(MigratedGroupRecovery.priority_tier.in_(tiers))
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
            for row_id in ids:
                row = session.get(MigratedGroupRecovery, int(row_id))
                if row is None:
                    continue
                row.readd_status = "processing"
                row.readd_attempted_at = now
                row.updated_at = now
            all_ids.extend(int(i) for i in ids)

        if not all_ids:
            return []

        session.commit()

        detail_rows = (
            session.query(MigratedGroupRecovery)
            .filter(MigratedGroupRecovery.id.in_(all_ids))
            .all()
        )

        club_order = {key: index for index, key in enumerate(RECOVERY_CLUB_KEYS)}
        detail_rows.sort(
            key=lambda row: (
                club_order.get(str(row.club_key), len(RECOVERY_CLUB_KEYS)),
                int(row.priority_tier),
                int(row.priority_rank),
            )
        )
        return [_recovery_row_from_model(row) for row in detail_rows]


def claim_next_pending_row(club_key: str) -> RecoveryRow | None:
    """Claim one pending row for a club (priority order, skip locked)."""

    from sqlalchemy import select

    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    tiers = recovery_priority_tiers_for_club(club_key)
    now = datetime.now(timezone.utc)
    with get_db() as session:
        row_id = session.execute(
            select(MigratedGroupRecovery.id)
            .where(MigratedGroupRecovery.readd_status == "pending")
            .where(MigratedGroupRecovery.club_key == club_key)
            .where(MigratedGroupRecovery.priority_tier.in_(tiers))
            .order_by(
                MigratedGroupRecovery.priority_tier.asc(),
                MigratedGroupRecovery.priority_rank.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        ).scalar_one_or_none()
        if row_id is None:
            return None
        row = session.get(MigratedGroupRecovery, int(row_id))
        if row is None:
            return None
        row.readd_status = "processing"
        row.readd_attempted_at = now
        row.updated_at = now
        session.commit()
        return _recovery_row_from_model(row)


def peek_next_recovery_rows(limit: int = 10) -> list[RecoveryRow]:
    """Read-only view of top pending rows in each club's tier scope."""

    from sqlalchemy import or_

    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    active_clubs = migration_recovery_active_club_keys()
    scope_filters = [
        (
            (MigratedGroupRecovery.club_key == club_key)
            & (MigratedGroupRecovery.priority_tier.in_(recovery_priority_tiers_for_club(club_key)))
        )
        for club_key in active_clubs
    ]
    if not scope_filters:
        return []

    with get_db() as session:
        detail_rows = (
            session.query(MigratedGroupRecovery)
            .filter(MigratedGroupRecovery.readd_status == "pending")
            .filter(or_(*scope_filters))
            .order_by(
                MigratedGroupRecovery.priority_tier.asc(),
                MigratedGroupRecovery.priority_rank.asc(),
            )
            .limit(max(1, int(limit)))
            .all()
        )
        return [_recovery_row_from_model(row) for row in detail_rows]


def is_migrated_recovery_chat(chat_id: int) -> bool:
    """True when chat is in the seeded migration-affected supergroup queue."""

    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    with get_db() as session:
        return (
            session.query(MigratedGroupRecovery.id)
            .filter_by(telegram_chat_id=int(chat_id))
            .first()
            is not None
        )


def format_whosnext_message(rows: list[RecoveryRow]) -> str:
    lines = ["Migration recovery — next 10 in queue", ""]
    if not rows:
        lines.append("(no pending rows)")
    else:
        for index, row in enumerate(rows, start=1):
            player = row.player_username or (
                str(row.player_telegram_user_id)
                if row.player_telegram_user_id is not None
                else "(unknown)"
            )
            if player and not str(player).startswith("@"):
                player = f"@{player}" if row.player_username else player
            lines.append(
                f"{index}. [{row.club_key}] {row.group_title}\n"
                f"   chat_id={row.telegram_chat_id}  player={player}"
            )
    disabled = sorted(get_migration_recovery_disabled_clubs())
    resume_at = get_rate_limit_resume_at()
    lines.extend(
        [
            "",
            f"Auto-add enabled: {'yes' if is_migration_recovery_enabled() else 'no'}",
            f"Auto-disabled (DB): {'yes' if is_migration_recovery_auto_disabled() else 'no'}",
            f"Active clubs: {', '.join(migration_recovery_active_club_keys()) or '(none)'}",
            f"Disabled clubs: {', '.join(disabled) if disabled else '(none)'}",
            "Tier scope: RT=tier 1+2, CC/GTO=tier 3",
            f"Batch size per club: {get_migration_recovery_batch_size()}",
        ]
    )
    if is_round_table_elevate_recovery_enabled():
        pending_elevate = count_elevate_pending_rows()
        lines.append(f"Elevate-pending (RT tier 1+2, has link): {pending_elevate}")
    if is_rate_limit_pause_pending() and resume_at is not None:
        lines.append(f"Rate-limit auto-resume at: {resume_at.isoformat()}")
    return "\n".join(lines)


def release_processing_rows(row_ids: list[int]) -> int:
    """Reset claimed-but-unprocessed rows back to pending."""

    if not row_ids:
        return 0
    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    now = datetime.now(timezone.utc)
    released = 0
    with get_db() as session:
        for row_id in row_ids:
            row = session.get(MigratedGroupRecovery, int(row_id))
            if row is None or row.readd_status != "processing":
                continue
            row.readd_status = "pending"
            row.readd_attempted_at = None
            row.updated_at = now
            released += 1
        session.commit()
    return released


def format_rate_limit_admin_notification(
    *,
    wait_s: int,
    label: str,
    row: RecoveryRow | None,
    resume_at: datetime | None = None,
) -> str:
    lines = [
        "[Migration recovery ops]",
        f"Telegram rate limit (FloodWait): {wait_s}s during {label}",
    ]
    if row is not None:
        lines.extend(
            [
                f"GC: {row.group_title}",
                f"club={row.club_key}",
                f"chat_id={row.telegram_chat_id}",
            ]
        )
    if resume_at is not None:
        lines.append(
            f"Recovery paused. Auto-resume at {resume_at.isoformat()} "
            "(1h after FloodWait ends; survives deploy/restart)."
        )
    else:
        lines.append(
            "Recovery auto-disabled. Clear DB flag and re-enable env to resume."
        )
    return "\n".join(lines)


async def _handle_rate_limit_abort(
    *,
    exc: FloodWaitAbortError,
    row: RecoveryRow,
    remaining_row_ids: list[int],
) -> None:
    err_result = ReaddGroupResult(
        chat_id=row.telegram_chat_id,
        club_id=row.club_id,
        club_key=row.club_key,
        title=row.group_title,
        member_count_before=0,
        member_count_after=None,
        status="error",
        error=f"flood_wait:{exc.label}:{exc.wait_s}s",
    )
    finalize_row(
        row.id,
        err_result,
        pre_status="failed",
        pre_error=f"flood_wait:{exc.label}:{exc.wait_s}s",
    )
    release_processing_rows(remaining_row_ids)

    resume_at = compute_rate_limit_resume_at(flood_wait_sec=exc.wait_s)
    detail = format_rate_limit_admin_notification(
        wait_s=exc.wait_s,
        label=exc.label,
        row=row,
        resume_at=resume_at,
    )
    from bot.services.mtproto_track_contact import notify_all_gc_admins_dm

    try:
        await notify_all_gc_admins_dm(detail)
    except Exception:
        logger.warning(
            "migration_recovery: rate-limit broadcast DM failed row_id=%s",
            row.id,
            exc_info=True,
        )

    from bot.services.slack_ops_notify import notify_slack_ops

    await notify_slack_ops(detail, source="migration_recovery")

    counts = pending_count_by_club(include_processing=True)
    await auto_disable_migration_recovery(
        reason="rate_limit",
        exhausted_club_key=row.club_key,
        pending_snapshot=counts,
        flood_wait_sec=exc.wait_s,
    )


def finalize_row(
    row_id: int,
    result: ReaddGroupResult,
    *,
    pre_status: str | None = None,
    pre_error: str | None = None,
    elevate: ElevateJoinResult | None = None,
    require_elevate: bool = False,
) -> str:
    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    if pre_status is not None:
        status = pre_status
        last_error = pre_error
        payload = {"pre_finalize": True, "error": pre_error}
        invite_link = None
    else:
        payload = build_readd_result_payload(result)
        if elevate is not None:
            payload = merge_elevate_into_payload(payload, elevate)
        status, last_error = map_readd_status_with_elevate(
            result,
            elevate=elevate,
            require_elevate=require_elevate,
        )
        invite_link = result.invite_link

    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = session.get(MigratedGroupRecovery, int(row_id))
        if row is None:
            return status
        row.readd_status = status
        row.readd_result = payload
        if invite_link is not None:
            row.invite_link = invite_link
        row.last_error = last_error
        if status == "processing":
            row.readd_completed_at = None
        else:
            row.readd_completed_at = now
        row.updated_at = now
        session.commit()
    return status


def finalize_elevate_only(row_id: int, elevate: ElevateJoinResult) -> str:
    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    now = datetime.now(timezone.utc)
    with get_db() as session:
        row = session.get(MigratedGroupRecovery, int(row_id))
        if row is None:
            return "failed"
        payload = dict(row.readd_result) if isinstance(row.readd_result, dict) else {}
        payload = merge_elevate_into_payload(payload, elevate)
        row.readd_result = payload
        if elevate.joined or elevate.already_member:
            row.readd_status = "complete"
            row.last_error = None
            row.readd_completed_at = now
            status = "complete"
        elif elevate.error:
            row.readd_status = "failed"
            row.last_error = f"elevate_join:{elevate.error}"
            row.readd_completed_at = now
            status = "failed"
        else:
            row.readd_status = "processing"
            status = "processing"
        row.updated_at = now
        session.commit()
    return status


async def _process_row(row: RecoveryRow) -> tuple[str, ReaddGroupResult]:
    from bot.services.mtproto_dm_gc_listener import get_listener_client

    cfg = get_club_gc_config_by_link_club_id(int(row.club_id))

    async def _finish(
        result: ReaddGroupResult,
        *,
        pre_status: str | None = None,
        pre_error: str | None = None,
        elevate: ElevateJoinResult | None = None,
        require_elevate: bool = False,
    ) -> tuple[str, ReaddGroupResult]:
        status = finalize_row(
            row.id,
            result,
            pre_status=pre_status,
            pre_error=pre_error,
            elevate=elevate,
            require_elevate=require_elevate,
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
        if cfg is not None or pre_error or status == "failed":
            await _notify_rt_ops_if_needed(
                row=row,
                result=result,
                terminal_status=status,
                pre_error=pre_error,
            )
        return status, result

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
    use_elevate_rt = (
        row.club_key == "round_table" and is_round_table_elevate_recovery_enabled()
    )
    readd_fn = readd_round_table_player_and_link if use_elevate_rt else readd_group
    result = await readd_fn(
        client=client,
        cfg=cfg,
        group=group,
        dialog_chat_id=int(row.telegram_chat_id),
        player_id=row.player_telegram_user_id,
        player_username=row.player_username,
        apply=True,
        update_invite_links=True,
        invite_staff=False,
        listener_user_id=listener_user_id,
        old_chat_id=int(row.old_chat_id),
    )
    if get_flood_wait_policy() == "abort":
        flood_exc = flood_wait_abort_from_readd_result(result)
        if flood_exc is not None:
            raise flood_exc
    try:
        maybe_persist_resolved_player_from_readd(row, result, cfg)
    except Exception:
        logger.warning(
            "migration_recovery: persist resolved player failed row_id=%s chat_id=%s",
            row.id,
            row.telegram_chat_id,
            exc_info=True,
        )
    return await _finish(result, require_elevate=use_elevate_rt)


async def _process_elevate_catchup() -> tuple[str, RecoveryRow | None]:
    """Elevate link-join on oldest RT row with invite link but no elevate_joined."""

    if not is_round_table_elevate_recovery_enabled():
        return "skipped", None

    row = find_oldest_elevate_pending_row()
    if row is None:
        return "skipped", None

    from bot.services.mtproto_dm_gc_listener import get_listener_client

    rt_client = get_listener_client("round_table")
    if rt_client is None or not rt_client.is_connected():
        logger.warning("migration_recovery: elevate catch-up skipped (RT listener down)")
        return "failed", row

    invite_link = _load_row_invite_link(row.id)
    if not invite_link:
        return "failed", row

    try:
        elevate = await elevate_join_recovery_group(
            invite_link=invite_link,
            dialog_chat_id=int(row.telegram_chat_id),
            rt_client=rt_client,
            apply=True,
        )
    except FloodWaitAbortError:
        raise

    status = finalize_elevate_only(row.id, elevate)
    logger.info(
        "migration_recovery elevate catch-up row_id=%s chat_id=%s status=%s error=%s",
        row.id,
        row.telegram_chat_id,
        status,
        elevate.error,
    )
    return status, row


async def tick_async() -> dict[str, int]:
    if not is_migration_recovery_enabled():
        return {"claimed": 0}

    active_clubs = migration_recovery_active_club_keys()
    summary: dict[str, int] = {
        "claimed": 0,
        "direct_add_quota_used": 0,
        "already_skipped": 0,
        "complete": 0,
        "privacy_blocked": 0,
        "failed": 0,
        "skipped": 0,
        "elevate_joined": 0,
        "elevate_failed": 0,
        "elevate_skipped": 0,
    }

    if not active_clubs:
        logger.info("migration_recovery: no active clubs")
        await _maybe_auto_disable_after_tick()
        return summary

    batch_size = get_migration_recovery_batch_size()
    delay = get_migration_recovery_invite_delay_sec()
    current_row: RecoveryRow | None = None
    flood_aborted = False

    async def _on_flood_wait(label: str, wait_s: int) -> None:
        detail = format_rate_limit_admin_notification(
            wait_s=wait_s,
            label=label,
            row=current_row,
            resume_at=compute_rate_limit_resume_at(flood_wait_sec=wait_s),
        )
        from bot.services.mtproto_track_contact import notify_all_gc_admins_dm

        try:
            await notify_all_gc_admins_dm(detail)
        except Exception:
            logger.warning(
                "migration_recovery: rate-limit notify failed label=%s",
                label,
                exc_info=True,
            )

    set_flood_wait_policy("abort")
    set_flood_wait_observer(_on_flood_wait)
    try:
        for club_key in active_clubs:
            if flood_aborted:
                break

            quota = batch_size
            club_processing_ids: list[int] = []
            need_delay_before_next = False

            while quota > 0:
                if need_delay_before_next and delay > 0:
                    await asyncio.sleep(delay)
                    need_delay_before_next = False

                row = claim_next_pending_row(club_key)
                if row is None:
                    break

                summary["claimed"] += 1
                club_processing_ids.append(row.id)
                current_row = row
                status = "failed"
                result = ReaddGroupResult(
                    chat_id=row.telegram_chat_id,
                    club_id=row.club_id,
                    club_key=row.club_key,
                    title=row.group_title,
                    member_count_before=0,
                    member_count_after=None,
                    status="error",
                    error="tick_exception",
                )

                try:
                    status, result = await _process_row(row)
                except FloodWaitAbortError as exc:
                    to_release = [
                        rid for rid in club_processing_ids if rid != row.id
                    ]
                    await _handle_rate_limit_abort(
                        exc=exc,
                        row=row,
                        remaining_row_ids=to_release,
                    )
                    summary["failed"] += 1
                    flood_aborted = True
                    break
                except Exception:
                    logger.exception(
                        "migration_recovery: tick failed row_id=%s chat_id=%s",
                        row.id,
                        row.telegram_chat_id,
                    )
                    status = finalize_row(row.id, result)
                    cfg = get_club_gc_config_by_link_club_id(int(row.club_id))
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
                        pre_error="tick_exception",
                    )

                if consumes_direct_add_quota(result):
                    quota -= 1
                    summary["direct_add_quota_used"] += 1
                    need_delay_before_next = True
                else:
                    summary["already_skipped"] += 1

                if status in summary:
                    summary[status] += 1
                else:
                    summary["failed"] += 1
                current_row = None

    finally:
        set_flood_wait_observer(None)
        set_flood_wait_policy("retry")

    if not flood_aborted and is_round_table_elevate_recovery_enabled():
        try:
            elevate_status, _elevate_row = await _process_elevate_catchup()
            if elevate_status == "complete":
                summary["elevate_joined"] += 1
            elif elevate_status == "failed":
                summary["elevate_failed"] += 1
            else:
                summary["elevate_skipped"] += 1
        except FloodWaitAbortError as exc:
            counts = pending_count_by_club(include_processing=True)
            await auto_disable_migration_recovery(
                reason="rate_limit",
                exhausted_club_key="round_table",
                pending_snapshot=counts,
                flood_wait_sec=exc.wait_s,
            )
            summary["elevate_failed"] += 1
            flood_aborted = True

    logger.info(
        "migration_recovery tick: claimed=%s direct_add=%s already_skipped=%s "
        "complete=%s privacy=%s failed=%s skipped=%s "
        "elevate_joined=%s elevate_failed=%s elevate_skipped=%s",
        summary["claimed"],
        summary["direct_add_quota_used"],
        summary["already_skipped"],
        summary["complete"],
        summary["privacy_blocked"],
        summary["failed"],
        summary["skipped"],
        summary["elevate_joined"],
        summary["elevate_failed"],
        summary["elevate_skipped"],
    )
    await _maybe_auto_disable_after_tick()
    record_migration_recovery_tick()
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


def migration_recovery_rate_limit_resume_callback(context) -> None:
    remove_migration_recovery_rate_limit_resume_job()
    resumed = maybe_apply_rate_limit_resume()
    if not resumed:
        logger.info("migration_recovery: rate-limit resume skipped (not due or cleared)")
        return
    if not is_migration_recovery_enabled():
        logger.info(
            "migration_recovery: rate-limit pause cleared but recovery still disabled"
        )
        return
    if _recovery_app is None:
        logger.warning("migration_recovery: rate-limit resume missing app reference")
        return
    schedule_migration_recovery_job(_recovery_app)
    logger.info("migration_recovery: resumed after rate-limit cooldown")


def schedule_migration_recovery_rate_limit_resume_job(app) -> None:
    global _recovery_app

    _recovery_app = app
    remove_migration_recovery_rate_limit_resume_job()
    delay_sec = compute_rate_limit_resume_delay_sec()
    if delay_sec is None:
        return
    app.job_queue.run_once(
        migration_recovery_rate_limit_resume_callback,
        when=timedelta(seconds=delay_sec),
        name="migration_recovery_rate_limit_resume",
    )
    logger.info(
        "migration_recovery rate-limit resume scheduled delay_sec=%s resume_at=%s",
        delay_sec,
        get_rate_limit_resume_at().isoformat() if get_rate_limit_resume_at() else None,
    )


def setup_migration_recovery_jobs(app) -> None:
    """Schedule recovery cron and DB-backed rate-limit resume after worker boot."""

    global _recovery_app

    from club_gc_settings import (
        _env_bool,
        is_dm_gc_listener_enabled,
        is_migration_recovery_enabled,
    )

    if not is_dm_gc_listener_enabled():
        return
    if not _env_bool("GC_MIGRATION_RECOVERY_ENABLED", default=False):
        return

    _recovery_app = app
    maybe_apply_rate_limit_resume()

    if is_migration_recovery_enabled():
        schedule_migration_recovery_job(app)
    elif is_rate_limit_pause_pending():
        schedule_migration_recovery_rate_limit_resume_job(app)


def schedule_migration_recovery_job(app) -> None:
    global _recovery_app

    from datetime import timedelta

    from club_gc_settings import get_migration_recovery_interval_sec

    _recovery_app = app
    remove_migration_recovery_rate_limit_resume_job()
    interval_sec = get_migration_recovery_interval_sec()
    first_delay_sec = compute_migration_recovery_first_delay_sec()
    app.job_queue.run_repeating(
        migration_recovery_job_callback,
        interval=timedelta(seconds=interval_sec),
        first=timedelta(seconds=first_delay_sec),
        name="migration_recovery",
    )
    logger.info(
        "migration_recovery job scheduled first_delay_sec=%s interval_sec=%s "
        "batch_size_per_club=%s active_clubs=%s disabled=%s",
        first_delay_sec,
        interval_sec,
        get_migration_recovery_batch_size(),
        ",".join(migration_recovery_active_club_keys()),
        ",".join(sorted(get_migration_recovery_disabled_clubs())) or "(none)",
    )


async def compute_recovery_slack_stats() -> list[ClubRecoverySlackStats]:
    from club_gc_settings import (
        CLUB_GC_CONFIG,
        get_migration_recovery_slack_summary_check_delay_sec,
    )
    from bot.services.recovery_membership_check import mtproto_scan_recovery_rows
    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    check_delay = get_migration_recovery_slack_summary_check_delay_sec()
    buckets: dict[str, dict[str, int]] = {
        club_key: {
            "total": 0,
            "left": 0,
            "done": 0,
            "in_group": 0,
            "in_group_pending": 0,
            "membership_finalized": 0,
            "check_errors": 0,
            "direct_added": 0,
            "invite_link": 0,
            "still_missing": 0,
        }
        for club_key in RECOVERY_CLUB_KEYS
    }

    with get_db() as session:
        rows = (
            session.query(MigratedGroupRecovery)
            .filter(MigratedGroupRecovery.priority_tier.in_(HIGH_PRIORITY_TIERS))
            .all()
        )
        scan_rows = [_recovery_slack_scan_row_from_model(row) for row in rows]

    for row in scan_rows:
        club_key = row.club_key
        if club_key not in buckets:
            continue
        bucket = buckets[club_key]
        bucket["total"] += 1
        status = row.readd_status
        if status in ("pending", "processing"):
            bucket["left"] += 1
        elif status in TERMINAL_STATUSES:
            bucket["done"] += 1

    rows_by_club: dict[str, list[_RecoverySlackScanRow]] = {
        k: [] for k in RECOVERY_CLUB_KEYS
    }
    row_by_id: dict[int, _RecoverySlackScanRow] = {}
    for row in scan_rows:
        club_key = row.club_key
        if club_key not in rows_by_club:
            continue
        rows_by_club[club_key].append(row)
        row_by_id[int(row.id)] = row

    for club_key, club_rows in rows_by_club.items():
        if not club_rows:
            continue
        logger.info(
            "migration_recovery: slack summary MTProto scan club=%s rows=%s",
            club_key,
            len(club_rows),
        )
        checks = await mtproto_scan_recovery_rows(
            club_key,
            club_rows,
            delay_sec=check_delay,
        )
        for row_id, check in checks.items():
            row = row_by_id.get(int(row_id))
            if row is None:
                continue
            bucket = buckets[row.club_key]
            if check.error:
                logger.warning(
                    "migration_recovery: slack summary check failed row_id=%s chat_id=%s err=%s",
                    row_id,
                    row.telegram_chat_id,
                    check.error,
                )
                bucket["check_errors"] += 1
                continue

            was_pending = row.readd_status in ("pending", "processing")
            finalized = False
            if check.player_in_group and was_pending:
                finalized = maybe_finalize_recovery_row_from_membership(
                    int(row_id),
                    eligible_player_ids=check.eligible_player_ids,
                )
                if finalized:
                    bucket["membership_finalized"] += 1
                    bucket["left"] -= 1
                    bucket["done"] += 1

            if check.player_in_group:
                bucket["in_group"] += 1
                if was_pending and not finalized:
                    bucket["in_group_pending"] += 1

            if finalized:
                outcome = "invite_link"
            else:
                outcome = classify_terminal_row_outcome(
                    readd_result=row.readd_result,
                    player_in_group=check.player_in_group,
                )
            if outcome == "direct_added":
                bucket["direct_added"] += 1
            elif outcome == "invite_link":
                bucket["invite_link"] += 1
            else:
                bucket["still_missing"] += 1

    total_finalized = sum(
        buckets[club_key]["membership_finalized"] for club_key in RECOVERY_CLUB_KEYS
    )
    if total_finalized:
        logger.info(
            "migration_recovery: slack summary finalized %s pending rows from MTProto",
            total_finalized,
        )

    stats: list[ClubRecoverySlackStats] = []
    for club_key in RECOVERY_CLUB_KEYS:
        bucket = buckets[club_key]
        if bucket["total"] == 0:
            continue
        display = CLUB_GC_CONFIG.get(club_key)
        stats.append(
            ClubRecoverySlackStats(
                club_key=club_key,
                club_display_name=(
                    display.club_display_name if display is not None else club_key
                ),
                total=int(bucket["total"]),
                left=int(bucket["left"]),
                done=int(bucket["done"]),
                pct_done=(100.0 * bucket["done"] / bucket["total"])
                if bucket["total"]
                else 0.0,
                in_group=int(bucket["in_group"]),
                pct_in_group=(100.0 * bucket["in_group"] / bucket["total"])
                if bucket["total"]
                else 0.0,
                in_group_pending=int(bucket["in_group_pending"]),
                check_errors=int(bucket["check_errors"]),
                direct_added=int(bucket["direct_added"]),
                invite_link=int(bucket["invite_link"]),
                still_missing=int(bucket["still_missing"]),
            )
        )
    return stats


def format_recovery_slack_summary(
    stats: list[ClubRecoverySlackStats],
) -> str:
    lines = ["Migration recovery progress (tier 1+2)", ""]
    for entry in stats:
        pct_ig = f"{entry.pct_in_group:.0f}%"
        pct_done = f"{entry.pct_done:.0f}%"
        lines.append(entry.club_display_name)
        lines.append(
            f"  in group: {pct_ig} ({entry.in_group}/{entry.total}) | "
            f"queue left: {entry.left} | queue done: {pct_done} "
            f"({entry.done}/{entry.total})"
        )
        if entry.in_group_pending:
            lines.append(
                f"  in group pending queue: {entry.in_group_pending}"
            )
        lines.append(
            "  direct added: "
            f"{entry.direct_added} | joined via link: {entry.invite_link} | "
            f"still missing: {entry.still_missing}"
        )
        if entry.check_errors:
            lines.append(f"  membership check errors: {entry.check_errors}")
        lines.append("")
    return "\n".join(lines).rstrip()


async def post_slack_recovery_summary() -> bool:
    if _recovery_app is None:
        logger.warning("migration_recovery: slack summary skipped (no app)")
        return False
    logger.info(
        "migration_recovery: MTProto membership sync + slack summary starting"
    )
    stats = await compute_recovery_slack_stats()
    if not stats:
        logger.info("migration_recovery: slack summary skipped (no tier 1+2 rows)")
        return False
    text = format_recovery_slack_summary(stats)
    from bot.services.slack_ops_notify import notify_slack_ops

    ok = await notify_slack_ops(text, source="migration_recovery")
    if not ok:
        logger.warning("migration_recovery: slack summary post failed")
        return False
    record_slack_summary_post()
    return True


async def migration_recovery_slack_summary_job_callback(_context) -> None:
    try:
        logger.info("migration_recovery: slack summary job starting")
        ok = await post_slack_recovery_summary()
        logger.info("migration_recovery: slack summary job finished ok=%s", ok)
    except Exception:
        logger.exception("migration_recovery: slack summary job failed")


def schedule_migration_recovery_slack_summary_job(app) -> None:
    global _recovery_app

    from datetime import timedelta

    from club_gc_settings import get_migration_recovery_slack_summary_interval_sec

    _recovery_app = app
    interval_sec = get_migration_recovery_slack_summary_interval_sec()
    first_delay_sec = compute_migration_recovery_slack_summary_first_delay_sec()
    app.job_queue.run_repeating(
        migration_recovery_slack_summary_job_callback,
        interval=timedelta(seconds=interval_sec),
        first=timedelta(seconds=first_delay_sec),
        name="migration_recovery_slack_summary",
    )
    logger.info(
        "migration_recovery slack summary scheduled first_delay_sec=%s interval_sec=%s",
        first_delay_sec,
        interval_sec,
    )


def fetch_club_recovery_queue_snapshots() -> list[ClubRecoveryQueueSnapshot]:
    """Per-club pending/skipped/failed counts for Slack queue snapshot."""
    from club_gc_settings import CLUB_GC_CONFIG
    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    buckets: dict[str, dict[str, int]] = {
        club_key: {
            "tier12_pending": 0,
            "tier3_pending": 0,
            "skipped": 0,
            "failed": 0,
            "processing": 0,
        }
        for club_key in RECOVERY_CLUB_KEYS
    }

    with get_db() as session:
        rows = session.query(
            MigratedGroupRecovery.club_key,
            MigratedGroupRecovery.priority_tier,
            MigratedGroupRecovery.readd_status,
        ).all()
        for club_key, tier, status in rows:
            key = str(club_key)
            if key not in buckets:
                continue
            bucket = buckets[key]
            st = str(status)
            t = int(tier)
            if st == "pending":
                if t in HIGH_PRIORITY_TIERS:
                    bucket["tier12_pending"] += 1
                elif t >= 3:
                    bucket["tier3_pending"] += 1
            elif st == "processing":
                bucket["processing"] += 1
            elif st == "skipped":
                bucket["skipped"] += 1
            elif st == "failed":
                bucket["failed"] += 1

    out: list[ClubRecoveryQueueSnapshot] = []
    for club_key in RECOVERY_CLUB_KEYS:
        bucket = buckets[club_key]
        total = sum(bucket.values())
        if total == 0:
            continue
        cfg = CLUB_GC_CONFIG.get(club_key)
        out.append(
            ClubRecoveryQueueSnapshot(
                club_key=club_key,
                club_display_name=(
                    cfg.club_display_name if cfg is not None else club_key
                ),
                tier12_pending=int(bucket["tier12_pending"]),
                tier3_pending=int(bucket["tier3_pending"]),
                skipped=int(bucket["skipped"]),
                failed=int(bucket["failed"]),
                processing=int(bucket["processing"]),
            )
        )
    return out


def format_recovery_queue_snapshot(
    snapshots: list[ClubRecoveryQueueSnapshot],
) -> str:
    if not snapshots:
        return ""
    lines = ["Queue snapshot (all tiers)", ""]
    for entry in snapshots:
        lines.append(entry.club_display_name)
        lines.append(
            f"  tier 1+2 pending: {entry.tier12_pending} | "
            f"tier 3 pending: {entry.tier3_pending} | processing: {entry.processing}"
        )
        lines.append(
            f"  skipped: {entry.skipped} | failed: {entry.failed}"
        )
        lines.append("")
    return "\n".join(lines).rstrip()


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
