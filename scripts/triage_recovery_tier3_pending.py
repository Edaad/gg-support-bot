"""Triage migration recovery rows by bot-observable activity and player discovery.

**Tier 3 pending** (``priority_tier=3``, ``readd_status=pending``):

- **Promote** when bot DB shows activity in the past N months.
- **Drop** inactive / deleted accounts.

**Tier 2 entity failures** (with ``--include-tier2-entity-failures``):
processed tier-2 rows in ``failed`` status whose ``last_error`` /
``readd_result`` indicate entity resolution failed (incl. Telethon
ValueError / dead username). Re-runs player discovery (latest eligible
non-support message on supergroup, then ``old_chat_id``) and can reset
the row to ``pending`` for the recovery worker.

Dry-run by default; pass ``--apply`` to write Postgres.

Usage:
  python scripts/triage_recovery_tier3_pending.py
  python scripts/triage_recovery_tier3_pending.py --club round_table
  python scripts/triage_recovery_tier3_pending.py --club round_table --include-tier2-entity-failures
  python scripts/triage_recovery_tier3_pending.py --row-id 42 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from bot.services.migration_recovery_priority import (  # noqa: E402
    classify_priority_tier,
    compute_priority_rank,
)
from bot.services.migration_group_readd import (  # noqa: E402
    FloodWaitAbortError,
    _username_marker,
    call_with_flood_retry,
    error_label,
    is_entity_resolution_error,
)
from scripts.migrated_groups_activity_report import (  # noqa: E402
    GroupAgg,
    MigratedGroupRow,
    _collect_activity,
)

logger = logging.getLogger("triage_recovery_tier3_pending")

CLUB_KEYS = ("round_table", "creator_club", "clubgto")

TriageAction = Literal[
    "promote",
    "drop_inactive",
    "drop_deleted",
    "repair_pending",
    "unchanged",
]

RowCohort = Literal["tier3_pending", "tier2_entity_failure"]

CSV_FIELDS = (
    "row_id",
    "cohort",
    "club_key",
    "telegram_chat_id",
    "player_telegram_user_id",
    "player_username",
    "readd_status",
    "row_last_error",
    "old_tier",
    "new_tier",
    "old_rank",
    "new_rank",
    "action",
    "last_activity_at",
    "activity_signals",
    "deposit_cents_in_window",
    "account_check",
    "discovered_player_id",
    "would_apply",
)

AccountCheck = Literal["alive", "deleted", "not_found", "uncheckable", "skipped_active"]


@dataclass(frozen=True)
class RecoveryRowForTriage:
    row_id: int
    cohort: RowCohort
    club_key: str
    club_id: int
    group_title: str
    telegram_chat_id: int
    old_chat_id: int
    player_telegram_user_id: int | None
    player_username: str | None
    priority_tier: int
    priority_rank: int
    readd_status: str
    row_last_error: str | None = None


@dataclass(frozen=True)
class PlayerAccountResolution:
    account_check: AccountCheck
    user_id: int | None = None
    username: str | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class TriageDecision:
    action: TriageAction
    new_tier: int
    new_rank: int
    last_error: str | None
    deposit_cents: int
    activity_epoch: int
    account_check: AccountCheck
    discovered_player_id: int | None = None
    discovered_username: str | None = None
    discovered_display_name: str | None = None


def _is_active_group(agg: GroupAgg | None) -> bool:
    if agg is None:
        return False
    return bool(agg.signals) or agg.has_payment_activity or agg.has_identifiable_user_activity


def _activity_epoch(agg: GroupAgg | None) -> int:
    if agg is None or agg.last_activity_at is None:
        return 0
    dt = agg.last_activity_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def account_check_from_resolved_user(
    user: Any | None,
    *,
    expected_user_id: int | None,
) -> AccountCheck:
    """Map a resolved Telethon user to alive / deleted / not_found."""
    if user is None:
        return "not_found"
    uid = getattr(user, "id", None)
    if expected_user_id is not None and uid is not None:
        if int(uid) != int(expected_user_id):
            return "not_found"
    if getattr(user, "deleted", False):
        return "deleted"
    return "alive"


def row_has_entity_resolution_failure(
    *,
    last_error: str | None,
    readd_result: dict[str, Any] | None,
) -> bool:
    """True when recovery failed due to Telethon entity / username resolution."""
    parts: list[str] = []
    if last_error:
        parts.append(str(last_error))
    payload = readd_result or {}
    if payload.get("error"):
        parts.append(str(payload["error"]))
    for item in payload.get("failed") or []:
        parts.append(str(item))
    text = " ".join(parts).lower()
    return (
        "entity_resolution_failed" in text
        or "could not find the input entity" in text
        or "no user has" in text
        or "username is not in use" in text
    )


def _user_to_resolution(
    user: Any | None,
    *,
    expected_user_id: int | None,
) -> PlayerAccountResolution:
    from bot.services.mtproto_group_player import format_telegram_user_display

    check = account_check_from_resolved_user(user, expected_user_id=expected_user_id)
    if user is None:
        return PlayerAccountResolution(account_check=check)
    uid = getattr(user, "id", None)
    display, un = format_telegram_user_display(user)
    return PlayerAccountResolution(
        account_check=check,
        user_id=int(uid) if uid is not None else None,
        username=(un or "").lstrip("@") or None,
        display_name=display,
    )


def classify_entity_failure_repair(
    resolution: PlayerAccountResolution,
) -> TriageDecision:
    """Tier-2 failed rows: rediscover player and queue retry, or drop if gone."""
    if resolution.account_check == "alive" and resolution.user_id is not None:
        return TriageDecision(
            action="repair_pending",
            new_tier=2,
            new_rank=0,
            last_error=None,
            deposit_cents=0,
            activity_epoch=0,
            account_check=resolution.account_check,
            discovered_player_id=resolution.user_id,
            discovered_username=resolution.username,
            discovered_display_name=resolution.display_name,
        )
    if resolution.account_check in ("deleted", "not_found"):
        return TriageDecision(
            action="drop_deleted",
            new_tier=2,
            new_rank=0,
            last_error="account_deleted",
            deposit_cents=0,
            activity_epoch=0,
            account_check=resolution.account_check,
        )
    return TriageDecision(
        action="unchanged",
        new_tier=2,
        new_rank=0,
        last_error=None,
        deposit_cents=0,
        activity_epoch=0,
        account_check=resolution.account_check,
    )


def _format_ts(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def classify_triage_action(
    *,
    agg: GroupAgg | None,
    account_check: AccountCheck,
) -> TriageDecision:
    """Pure classification from activity aggregate and account check result."""
    active = _is_active_group(agg)
    deposit_cents = int(agg.total_deposited_cents) if agg else 0
    activity_epoch = _activity_epoch(agg)

    if active:
        new_tier = classify_priority_tier(
            deposit_cents=deposit_cents,
            active_in_past_30_days=True,
        )
        return TriageDecision(
            action="promote",
            new_tier=new_tier,
            new_rank=0,
            last_error=None,
            deposit_cents=deposit_cents,
            activity_epoch=activity_epoch,
            account_check="skipped_active",
        )

    if account_check in ("deleted", "not_found"):
        return TriageDecision(
            action="drop_deleted",
            new_tier=3,
            new_rank=0,
            last_error="account_deleted",
            deposit_cents=0,
            activity_epoch=0,
            account_check=account_check,
        )

    return TriageDecision(
        action="drop_inactive",
        new_tier=3,
        new_rank=0,
        last_error="inactive_no_bot_activity",
        deposit_cents=0,
        activity_epoch=0,
        account_check=account_check,
    )


def finalize_triage_decision(
    decision: TriageDecision,
    *,
    row_id: int,
    telegram_chat_id: int,
    old_tier: int,
    old_rank: int,
) -> TriageDecision:
    """Attach rank for promote actions; mark unchanged when no DB write needed."""
    if decision.action == "promote":
        new_rank = compute_priority_rank(
            priority_tier=decision.new_tier,
            deposit_cents=decision.deposit_cents,
            last_activity_epoch=decision.activity_epoch,
            telegram_chat_id=int(telegram_chat_id),
            sequence=int(row_id),
        )
        if decision.new_tier == old_tier and new_rank == old_rank:
            return TriageDecision(
                action="unchanged",
                new_tier=old_tier,
                new_rank=old_rank,
                last_error=None,
                deposit_cents=decision.deposit_cents,
                activity_epoch=decision.activity_epoch,
                account_check=decision.account_check,
            )
        return TriageDecision(
            action="promote",
            new_tier=decision.new_tier,
            new_rank=new_rank,
            last_error=None,
            deposit_cents=decision.deposit_cents,
            activity_epoch=decision.activity_epoch,
            account_check=decision.account_check,
        )
    return decision


def build_triage_csv_row(
    row: RecoveryRowForTriage,
    decision: TriageDecision,
    *,
    agg: GroupAgg | None,
    apply: bool,
) -> dict[str, Any]:
    signals = ",".join(sorted(agg.signals)) if agg and agg.signals else ""
    would_apply = decision.action in (
        "promote",
        "drop_inactive",
        "drop_deleted",
        "repair_pending",
    )
    return {
        "row_id": row.row_id,
        "cohort": row.cohort,
        "club_key": row.club_key,
        "telegram_chat_id": row.telegram_chat_id,
        "player_telegram_user_id": row.player_telegram_user_id or "",
        "player_username": row.player_username or "",
        "readd_status": row.readd_status,
        "row_last_error": row.row_last_error or "",
        "old_tier": row.priority_tier,
        "new_tier": decision.new_tier if decision.action == "promote" else row.priority_tier,
        "old_rank": row.priority_rank,
        "new_rank": decision.new_rank if decision.action == "promote" else row.priority_rank,
        "action": decision.action,
        "last_activity_at": _format_ts(agg.last_activity_at if agg else None),
        "activity_signals": signals,
        "deposit_cents_in_window": decision.deposit_cents,
        "account_check": decision.account_check,
        "discovered_player_id": decision.discovered_player_id or "",
        "would_apply": "yes" if (would_apply and apply) else ("would" if would_apply else "no"),
    }


def _default_output_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _REPO_ROOT / "backups" / f"recovery_tier3_triage_{stamp}.csv"


def _row_from_orm(r, *, cohort: RowCohort) -> RecoveryRowForTriage:
    return RecoveryRowForTriage(
        row_id=int(r.id),
        cohort=cohort,
        club_key=str(r.club_key),
        club_id=int(r.club_id),
        group_title=str(r.group_title or ""),
        telegram_chat_id=int(r.telegram_chat_id),
        old_chat_id=int(r.old_chat_id),
        player_telegram_user_id=(
            int(r.player_telegram_user_id) if r.player_telegram_user_id else None
        ),
        player_username=(str(r.player_username) if r.player_username else None),
        priority_tier=int(r.priority_tier),
        priority_rank=int(r.priority_rank),
        readd_status=str(r.readd_status),
        row_last_error=(str(r.last_error) if r.last_error else None),
    )


def _load_recovery_rows(
    *,
    club_filter: str | None,
    row_id: int | None,
    limit: int | None,
    include_tier3_pending: bool,
    include_tier2_entity_failures: bool,
) -> list[RecoveryRowForTriage]:
    from db.connection import get_db, init_engine
    from db.models import MigratedGroupRecovery

    if not include_tier3_pending and not include_tier2_entity_failures:
        return []

    init_engine()
    out: list[RecoveryRowForTriage] = []
    with get_db() as session:
        if include_tier3_pending:
            q = session.query(MigratedGroupRecovery).filter(
                MigratedGroupRecovery.priority_tier == 3,
                MigratedGroupRecovery.readd_status == "pending",
            )
            if row_id is not None:
                q = q.filter(MigratedGroupRecovery.id == int(row_id))
            if club_filter:
                q = q.filter(MigratedGroupRecovery.club_key == club_filter)
            for r in q.order_by(
                MigratedGroupRecovery.club_key,
                MigratedGroupRecovery.priority_rank,
                MigratedGroupRecovery.id,
            ).all():
                out.append(_row_from_orm(r, cohort="tier3_pending"))

        if include_tier2_entity_failures:
            q = session.query(MigratedGroupRecovery).filter(
                MigratedGroupRecovery.priority_tier == 2,
                MigratedGroupRecovery.readd_status == "failed",
            )
            if row_id is not None:
                q = q.filter(MigratedGroupRecovery.id == int(row_id))
            if club_filter:
                q = q.filter(MigratedGroupRecovery.club_key == club_filter)
            for r in q.order_by(
                MigratedGroupRecovery.club_key,
                MigratedGroupRecovery.id,
            ).all():
                if not row_has_entity_resolution_failure(
                    last_error=r.last_error,
                    readd_result=r.readd_result,
                ):
                    continue
                out.append(_row_from_orm(r, cohort="tier2_entity_failure"))

    out.sort(key=lambda row: (row.club_key, row.cohort, row.priority_rank, row.row_id))
    if limit is not None:
        return out[: int(limit)]
    return out


def _rows_to_migrated_groups(rows: list[RecoveryRowForTriage]) -> list[MigratedGroupRow]:
    return [
        MigratedGroupRow(
            club_id=int(r.club_id),
            club_key=str(r.club_key),
            group_title=str(r.group_title),
            old_chat_id=int(r.old_chat_id),
            current_chat_id=int(r.telegram_chat_id),
        )
        for r in rows
    ]


async def _discover_player_from_group_messages(
    client: Any,
    cfg: Any,
    *,
    telegram_chat_id: int,
    old_chat_id: int | None,
    self_id: int | None,
) -> Any | None:
    """Find player via latest eligible non-support message (current chat, then old)."""
    from bot.services.mtproto_group_player import find_latest_eligible_message_sender

    current_entity = None
    try:
        current_entity = await call_with_flood_retry(
            lambda: client.get_entity(int(telegram_chat_id)),
            label=f"get_entity:chat:{telegram_chat_id}",
        )
    except Exception as e:
        if not is_entity_resolution_error(e):
            raise

    if current_entity is not None:
        user = await find_latest_eligible_message_sender(
            client,
            current_entity,
            cfg,
            self_id=self_id,
        )
        if user is not None:
            return user

    if old_chat_id is None:
        return None

    old_entity = None
    try:
        old_entity = await call_with_flood_retry(
            lambda: client.get_entity(int(old_chat_id)),
            label=f"get_entity:old_chat:{old_chat_id}",
        )
    except Exception as e:
        if not is_entity_resolution_error(e):
            raise

    if old_entity is None:
        return None

    return await find_latest_eligible_message_sender(
        client,
        old_entity,
        cfg,
        self_id=self_id,
    )


async def _resolve_player_account(
    client: Any,
    cfg: Any,
    *,
    player_telegram_user_id: int | None,
    player_username: str | None,
    telegram_chat_id: int,
    old_chat_id: int | None,
    self_id: int | None,
) -> PlayerAccountResolution:
    from bot.services.migration_group_readd import resolve_player_entity_for_readd

    if player_telegram_user_id is not None:
        channel_entity = None
        try:
            channel_entity = await call_with_flood_retry(
                lambda: client.get_entity(int(telegram_chat_id)),
                label=f"get_entity:chat:{telegram_chat_id}",
            )
        except Exception as e:
            if not is_entity_resolution_error(e):
                raise

        scan_old_chat_id = old_chat_id
        if channel_entity is None and old_chat_id is not None:
            try:
                channel_entity = await call_with_flood_retry(
                    lambda: client.get_entity(int(old_chat_id)),
                    label=f"get_entity:old_chat:{old_chat_id}",
                )
                scan_old_chat_id = None
            except Exception as e:
                if not is_entity_resolution_error(e):
                    raise

        if channel_entity is not None:
            user, _source = await resolve_player_entity_for_readd(
                client,
                channel_entity,
                cfg,
                stored_id=int(player_telegram_user_id),
                stored_username=player_username,
                self_id=self_id,
                old_chat_id=scan_old_chat_id,
            )
            resolution = _user_to_resolution(
                user,
                expected_user_id=player_telegram_user_id,
            )
            if resolution.account_check != "not_found":
                return resolution

    elif player_username:
        username_marker = _username_marker(player_username)
        if username_marker:
            try:
                user = await call_with_flood_retry(
                    lambda: client.get_entity(username_marker),
                    label=f"get_entity:{username_marker}",
                )
                resolution = _user_to_resolution(user, expected_user_id=None)
                if resolution.account_check != "not_found":
                    return resolution
            except Exception as e:
                if not is_entity_resolution_error(e):
                    raise

    user = await _discover_player_from_group_messages(
        client,
        cfg,
        telegram_chat_id=int(telegram_chat_id),
        old_chat_id=old_chat_id,
        self_id=self_id,
    )
    return _user_to_resolution(
        user,
        expected_user_id=player_telegram_user_id,
    )


async def _check_accounts_for_club(
    club_key: str,
    rows: list[RecoveryRowForTriage],
    *,
    delay_sec: float,
) -> dict[int, PlayerAccountResolution]:
    from club_gc_settings import CLUB_GC_CONFIG
    from bot.services.mtproto_group_create import (
        get_mtproto_lock,
        is_client_authorized,
        make_client,
    )

    out: dict[int, PlayerAccountResolution] = {}
    if not rows:
        return out

    cfg = CLUB_GC_CONFIG.get(club_key)
    if cfg is None:
        for row in rows:
            out[row.row_id] = PlayerAccountResolution(account_check="uncheckable")
        return out

    if not await is_client_authorized(cfg):
        for row in rows:
            out[row.row_id] = PlayerAccountResolution(account_check="uncheckable")
        return out

    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                for row in rows:
                    out[row.row_id] = PlayerAccountResolution(account_check="uncheckable")
                return out

            me = await client.get_me()
            self_id = int(me.id) if me and getattr(me, "id", None) is not None else None

            for i, row in enumerate(rows, 1):
                try:
                    out[row.row_id] = await _resolve_player_account(
                        client,
                        cfg,
                        player_telegram_user_id=row.player_telegram_user_id,
                        player_username=row.player_username,
                        telegram_chat_id=int(row.telegram_chat_id),
                        old_chat_id=int(row.old_chat_id) if row.old_chat_id else None,
                        self_id=self_id,
                    )
                except Exception as e:
                    if isinstance(e, FloodWaitAbortError):
                        raise
                    logger.warning(
                        "account check failed row_id=%s chat_id=%s: %s",
                        row.row_id,
                        row.telegram_chat_id,
                        error_label(e),
                    )
                    out[row.row_id] = PlayerAccountResolution(account_check="uncheckable")
                if i % 25 == 0:
                    print(f"  {club_key}: account-checked {i}/{len(rows)}", flush=True)
                if delay_sec > 0:
                    await asyncio.sleep(delay_sec)
        finally:
            await client.disconnect()
    return out


def _apply_triage_decisions(
    decisions: list[tuple[RecoveryRowForTriage, TriageDecision]],
) -> dict[str, int]:
    from club_gc_settings import CLUB_GC_CONFIG
    from bot.services.migration_recovery import persist_resolved_recovery_player
    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    counts: dict[str, int] = defaultdict(int)
    now = datetime.now(timezone.utc)

    with get_db() as session:
        for row, decision in decisions:
            if decision.action == "unchanged":
                counts["unchanged"] += 1
                continue

            db_row = session.get(MigratedGroupRecovery, int(row.row_id))
            if db_row is None:
                counts["missing"] += 1
                continue

            if row.cohort == "tier3_pending":
                if db_row.readd_status != "pending" or int(db_row.priority_tier) != 3:
                    counts["stale"] += 1
                    continue
                if decision.action == "promote":
                    db_row.priority_tier = int(decision.new_tier)
                    db_row.priority_rank = int(decision.new_rank)
                    db_row.updated_at = now
                    counts["promoted"] += 1
                elif decision.action in ("drop_inactive", "drop_deleted"):
                    db_row.readd_status = "skipped"
                    db_row.last_error = decision.last_error
                    db_row.readd_completed_at = now
                    db_row.updated_at = now
                    counts[decision.action] += 1
                continue

            if row.cohort == "tier2_entity_failure":
                if (
                    int(db_row.priority_tier) != 2
                    or str(db_row.readd_status) != "failed"
                ):
                    counts["stale"] += 1
                    continue
                if decision.action == "repair_pending":
                    if decision.discovered_player_id is None:
                        counts["stale"] += 1
                        continue
                    cfg = CLUB_GC_CONFIG.get(row.club_key)
                    club_display = (
                        cfg.club_display_name if cfg is not None else row.club_key
                    )
                    persist_resolved_recovery_player(
                        row_id=row.row_id,
                        club_key=row.club_key,
                        club_display_name=club_display,
                        telegram_chat_id=int(row.telegram_chat_id),
                        group_title=row.group_title,
                        player_id=int(decision.discovered_player_id),
                        player_username=decision.discovered_username,
                        player_display_name=decision.discovered_display_name,
                    )
                    db_row = session.get(MigratedGroupRecovery, int(row.row_id))
                    if db_row is None:
                        counts["missing"] += 1
                        continue
                    db_row.readd_status = "pending"
                    db_row.last_error = None
                    db_row.readd_attempted_at = None
                    db_row.readd_completed_at = None
                    db_row.updated_at = now
                    counts["repair_pending"] += 1
                elif decision.action == "drop_deleted":
                    db_row.readd_status = "skipped"
                    db_row.last_error = decision.last_error
                    db_row.readd_completed_at = now
                    db_row.updated_at = now
                    counts["drop_deleted"] += 1
        session.commit()
    return dict(counts)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"Wrote {len(rows)} rows to {path}")


def _build_summary_lines(csv_rows: list[dict[str, Any]], *, mode: str) -> list[str]:
    counts: dict[str, int] = defaultdict(int)
    for row in csv_rows:
        counts[str(row["action"])] += 1
    lines = [
        f"Migration recovery triage — {mode}",
        f"Total rows: {len(csv_rows)}",
        f"  promote: {counts.get('promote', 0)}",
        f"  repair_pending: {counts.get('repair_pending', 0)}",
        f"  drop_inactive: {counts.get('drop_inactive', 0)}",
        f"  drop_deleted: {counts.get('drop_deleted', 0)}",
        f"  unchanged: {counts.get('unchanged', 0)}",
    ]
    by_club: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in csv_rows:
        by_club[str(row["club_key"])][str(row["action"])] += 1
    for club in CLUB_KEYS:
        if club not in by_club:
            continue
        c = by_club[club]
        lines.append(
            f"  {club}: promote={c.get('promote', 0)} "
            f"repair={c.get('repair_pending', 0)} "
            f"drop_inactive={c.get('drop_inactive', 0)} "
            f"drop_deleted={c.get('drop_deleted', 0)}"
        )
    return lines


async def run_triage(
    *,
    months: int,
    club_filter: str | None,
    row_id: int | None,
    limit: int | None,
    apply: bool,
    delay_sec: float,
    include_tier3_pending: bool,
    include_tier2_entity_failures: bool,
) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
    rows = _load_recovery_rows(
        club_filter=club_filter,
        row_id=row_id,
        limit=limit,
        include_tier3_pending=include_tier3_pending,
        include_tier2_entity_failures=include_tier2_entity_failures,
    )
    if not rows:
        return [], None

    tier3_rows = [r for r in rows if r.cohort == "tier3_pending"]
    tier2_rows = [r for r in rows if r.cohort == "tier2_entity_failure"]

    days = int(months) * 30
    activity_by_chat: dict[int, GroupAgg] = {}
    if tier3_rows:
        groups = _rows_to_migrated_groups(tier3_rows)
        activity_by_chat, _user_aggs = _collect_activity(groups, days=days)

    by_club: dict[str, list[RecoveryRowForTriage]] = defaultdict(list)
    for row in rows:
        by_club[row.club_key].append(row)

    account_resolutions: dict[int, PlayerAccountResolution] = {}
    club_order = [club_filter] if club_filter else [k for k in CLUB_KEYS if k in by_club]
    for club_key in club_order:
        club_rows = by_club.get(club_key, [])
        if not club_rows:
            continue
        tier3_inactive = [
            row
            for row in club_rows
            if row.cohort == "tier3_pending"
            and not _is_active_group(activity_by_chat.get(int(row.telegram_chat_id)))
        ]
        tier2_repair = [row for row in club_rows if row.cohort == "tier2_entity_failure"]
        check_rows = tier3_inactive + tier2_repair
        if check_rows:
            print(
                f"MTProto player discovery for {club_key} "
                f"({len(tier3_inactive)} tier3 inactive, "
                f"{len(tier2_repair)} tier2 entity failures)...",
                flush=True,
            )
        account_resolutions.update(
            await _check_accounts_for_club(club_key, check_rows, delay_sec=delay_sec)
        )

    decisions: list[tuple[RecoveryRowForTriage, TriageDecision]] = []
    csv_rows: list[dict[str, Any]] = []

    for row in rows:
        agg = activity_by_chat.get(int(row.telegram_chat_id))
        resolution = account_resolutions.get(
            row.row_id,
            PlayerAccountResolution(account_check="skipped_active"),
        )

        if row.cohort == "tier2_entity_failure":
            decision = classify_entity_failure_repair(resolution)
        else:
            account_check = resolution.account_check
            if _is_active_group(agg):
                account_check = "skipped_active"
            raw = classify_triage_action(agg=agg, account_check=account_check)
            decision = finalize_triage_decision(
                raw,
                row_id=row.row_id,
                telegram_chat_id=row.telegram_chat_id,
                old_tier=row.priority_tier,
                old_rank=row.priority_rank,
            )
            if resolution.user_id is not None and decision.action in (
                "drop_inactive",
                "drop_deleted",
            ):
                decision = TriageDecision(
                    action=decision.action,
                    new_tier=decision.new_tier,
                    new_rank=decision.new_rank,
                    last_error=decision.last_error,
                    deposit_cents=decision.deposit_cents,
                    activity_epoch=decision.activity_epoch,
                    account_check=account_check,
                    discovered_player_id=resolution.user_id,
                    discovered_username=resolution.username,
                    discovered_display_name=resolution.display_name,
                )

        decisions.append((row, decision))
        csv_rows.append(
            build_triage_csv_row(row, decision, agg=agg, apply=apply)
        )

    apply_counts = None
    if apply:
        apply_counts = _apply_triage_decisions(decisions)

    return csv_rows, apply_counts


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--months",
        type=int,
        default=3,
        help="Activity lookback in months (default 3, uses days=months*30)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write tier/rank promotions and skipped drops to Postgres",
    )
    parser.add_argument(
        "--row-id",
        type=int,
        default=None,
        help="Triage one migrated_group_recovery row (test before bulk)",
    )
    parser.add_argument(
        "--club",
        choices=CLUB_KEYS,
        default=None,
        help="Filter to one club",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap number of rows processed",
    )
    parser.add_argument(
        "--delay-sec",
        type=float,
        default=0.05,
        help="Sleep between MTProto account checks (default 0.05)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Write audit CSV (default: backups/recovery_tier3_triage_<ts>.csv)",
    )
    parser.add_argument(
        "--include-tier2-entity-failures",
        action="store_true",
        help="Also repair tier-2 failed rows with entity_resolution_failed / ValueError",
    )
    parser.add_argument(
        "--tier2-entity-failures-only",
        action="store_true",
        help="Only tier-2 entity-failure repair (skip tier-3 pending triage)",
    )
    args = parser.parse_args()

    include_tier2 = bool(
        args.include_tier2_entity_failures or args.tier2_entity_failures_only
    )
    include_tier3 = not args.tier2_entity_failures_only

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    mode = "APPLY" if args.apply else "DRY-RUN"
    csv_rows, apply_counts = await run_triage(
        months=int(args.months),
        club_filter=args.club,
        row_id=args.row_id,
        limit=args.limit,
        apply=bool(args.apply),
        delay_sec=float(args.delay_sec),
        include_tier3_pending=include_tier3,
        include_tier2_entity_failures=include_tier2,
    )

    if not csv_rows:
        print("No recovery rows matched filters.")
        return 0

    for line in _build_summary_lines(csv_rows, mode=mode):
        print(line)

    if apply_counts is not None:
        print("DB apply results:")
        for key in (
            "promoted",
            "repair_pending",
            "drop_inactive",
            "drop_deleted",
            "unchanged",
            "stale",
            "missing",
        ):
            if apply_counts.get(key):
                print(f"  {key}: {apply_counts[key]}")

    output_path = args.output or _default_output_path()
    _write_csv(output_path, csv_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
