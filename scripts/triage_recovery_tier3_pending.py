"""Triage tier-3 pending migration recovery rows by bot-observable activity.

For each ``migrated_group_recovery`` row with ``priority_tier=3`` and
``readd_status=pending``:

- **Promote** when bot DB shows activity in the past N months (deposits, cashouts,
  payments, binds) — same signals as ``seed_migrated_group_recovery``.
- **Drop** inactive rows (``readd_status=skipped``).
- **Drop** deleted/unresolvable Telegram accounts (MTProto entity check).

Dry-run by default; pass ``--apply`` to write Postgres.

Do not run MTProto account checks while the Heroku worker holds the same club
Telethon session unless ``GC_MTPROTO_ENABLED=false`` on the worker.

Environment: DATABASE_URL; TG_API_ID, TG_API_HASH for inactive-row account checks.

Usage:
  python scripts/triage_recovery_tier3_pending.py
  python scripts/triage_recovery_tier3_pending.py --row-id 42
  python scripts/triage_recovery_tier3_pending.py --club clubgto --months 3
  python scripts/triage_recovery_tier3_pending.py --limit 1 --apply
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
    _username_marker,
    call_with_flood_retry,
    is_entity_resolution_error,
)
from scripts.migrated_groups_activity_report import (  # noqa: E402
    GroupAgg,
    MigratedGroupRow,
    _collect_activity,
)

logger = logging.getLogger("triage_recovery_tier3_pending")

CLUB_KEYS = ("round_table", "creator_club", "clubgto")

TriageAction = Literal["promote", "drop_inactive", "drop_deleted", "unchanged"]

CSV_FIELDS = (
    "row_id",
    "club_key",
    "telegram_chat_id",
    "player_telegram_user_id",
    "player_username",
    "old_tier",
    "new_tier",
    "old_rank",
    "new_rank",
    "action",
    "last_activity_at",
    "activity_signals",
    "deposit_cents_in_window",
    "account_check",
    "would_apply",
)

AccountCheck = Literal["alive", "deleted", "not_found", "uncheckable", "skipped_active"]


@dataclass(frozen=True)
class RecoveryRowForTriage:
    row_id: int
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


@dataclass(frozen=True)
class TriageDecision:
    action: TriageAction
    new_tier: int
    new_rank: int
    last_error: str | None
    deposit_cents: int
    activity_epoch: int
    account_check: AccountCheck


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
    would_apply = decision.action in ("promote", "drop_inactive", "drop_deleted")
    return {
        "row_id": row.row_id,
        "club_key": row.club_key,
        "telegram_chat_id": row.telegram_chat_id,
        "player_telegram_user_id": row.player_telegram_user_id or "",
        "player_username": row.player_username or "",
        "old_tier": row.priority_tier,
        "new_tier": decision.new_tier if decision.action == "promote" else row.priority_tier,
        "old_rank": row.priority_rank,
        "new_rank": decision.new_rank if decision.action == "promote" else row.priority_rank,
        "action": decision.action,
        "last_activity_at": _format_ts(agg.last_activity_at if agg else None),
        "activity_signals": signals,
        "deposit_cents_in_window": decision.deposit_cents,
        "account_check": decision.account_check,
        "would_apply": "yes" if (would_apply and apply) else ("would" if would_apply else "no"),
    }


def _default_output_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _REPO_ROOT / "backups" / f"recovery_tier3_triage_{stamp}.csv"


def _load_tier3_pending_rows(
    *,
    club_filter: str | None,
    row_id: int | None,
    limit: int | None,
) -> list[RecoveryRowForTriage]:
    from db.connection import get_db, init_engine
    from db.models import MigratedGroupRecovery

    init_engine()
    with get_db() as session:
        q = session.query(MigratedGroupRecovery).filter(
            MigratedGroupRecovery.priority_tier == 3,
            MigratedGroupRecovery.readd_status == "pending",
        )
        if row_id is not None:
            q = q.filter(MigratedGroupRecovery.id == int(row_id))
        if club_filter:
            q = q.filter(MigratedGroupRecovery.club_key == club_filter)
        q = q.order_by(
            MigratedGroupRecovery.club_key,
            MigratedGroupRecovery.priority_rank,
            MigratedGroupRecovery.id,
        )
        if limit is not None:
            q = q.limit(int(limit))
        rows = q.all()
        return [
            RecoveryRowForTriage(
                row_id=int(r.id),
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
            )
            for r in rows
        ]


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


async def _resolve_player_account(
    client: Any,
    *,
    player_telegram_user_id: int | None,
    player_username: str | None,
) -> AccountCheck:
    if player_telegram_user_id is None and not (player_username or "").strip():
        return "uncheckable"

    if player_telegram_user_id is not None:
        try:
            user = await call_with_flood_retry(
                lambda: client.get_entity(int(player_telegram_user_id)),
                label=f"get_entity:{player_telegram_user_id}",
            )
            if getattr(user, "deleted", False):
                return "deleted"
            return "alive"
        except Exception as e:
            if not is_entity_resolution_error(e):
                raise

    username_marker = _username_marker(player_username)
    if username_marker:
        try:
            user = await call_with_flood_retry(
                lambda: client.get_entity(username_marker),
                label=f"get_entity:{username_marker}",
            )
            if getattr(user, "deleted", False):
                return "deleted"
            return "alive"
        except Exception as e:
            if not is_entity_resolution_error(e):
                raise
            return "not_found"

    return "not_found" if player_telegram_user_id is not None else "uncheckable"


async def _check_accounts_for_club(
    club_key: str,
    items: list[tuple[RecoveryRowForTriage, GroupAgg | None]],
    *,
    delay_sec: float,
) -> dict[int, AccountCheck]:
    from club_gc_settings import CLUB_GC_CONFIG
    from bot.services.mtproto_group_create import (
        get_mtproto_lock,
        is_client_authorized,
        make_client,
    )

    inactive = [
        (row, agg)
        for row, agg in items
        if not _is_active_group(agg)
    ]
    out: dict[int, AccountCheck] = {}
    if not inactive:
        return out

    cfg = CLUB_GC_CONFIG.get(club_key)
    if cfg is None:
        for row, _agg in inactive:
            out[row.row_id] = "uncheckable"
        return out

    if not await is_client_authorized(cfg):
        for row, _agg in inactive:
            out[row.row_id] = "uncheckable"
        return out

    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                for row, _agg in inactive:
                    out[row.row_id] = "uncheckable"
                return out

            for i, (row, _agg) in enumerate(inactive, 1):
                out[row.row_id] = await _resolve_player_account(
                    client,
                    player_telegram_user_id=row.player_telegram_user_id,
                    player_username=row.player_username,
                )
                if i % 25 == 0:
                    print(f"  {club_key}: account-checked {i}/{len(inactive)}", flush=True)
                if delay_sec > 0:
                    await asyncio.sleep(delay_sec)
        finally:
            await client.disconnect()
    return out


def _apply_triage_decisions(
    decisions: list[tuple[RecoveryRowForTriage, TriageDecision]],
) -> dict[str, int]:
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
        f"Tier-3 pending recovery triage — {mode}",
        f"Total rows: {len(csv_rows)}",
        f"  promote: {counts.get('promote', 0)}",
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
) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
    rows = _load_tier3_pending_rows(
        club_filter=club_filter,
        row_id=row_id,
        limit=limit,
    )
    if not rows:
        return [], None

    days = int(months) * 30
    groups = _rows_to_migrated_groups(rows)
    activity_by_chat, _user_aggs = _collect_activity(groups, days=days)

    by_club: dict[str, list[tuple[RecoveryRowForTriage, GroupAgg | None]]] = defaultdict(list)
    for row in rows:
        agg = activity_by_chat.get(int(row.telegram_chat_id))
        by_club[row.club_key].append((row, agg))

    account_checks: dict[int, AccountCheck] = {}
    club_order = [club_filter] if club_filter else [k for k in CLUB_KEYS if k in by_club]
    for club_key in club_order:
        club_items = by_club.get(club_key, [])
        if not club_items:
            continue
        inactive_count = sum(1 for _row, agg in club_items if not _is_active_group(agg))
        if inactive_count:
            print(
                f"MTProto account check for {club_key} ({inactive_count} inactive rows)...",
                flush=True,
            )
        account_checks.update(
            await _check_accounts_for_club(club_key, club_items, delay_sec=delay_sec)
        )

    decisions: list[tuple[RecoveryRowForTriage, TriageDecision]] = []
    csv_rows: list[dict[str, Any]] = []

    for row in rows:
        agg = activity_by_chat.get(int(row.telegram_chat_id))
        account_check = account_checks.get(row.row_id, "skipped_active")
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
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    mode = "APPLY" if args.apply else "DRY-RUN"
    csv_rows, apply_counts = await run_triage(
        months=int(args.months),
        club_filter=args.club,
        row_id=args.row_id,
        limit=args.limit,
        apply=bool(args.apply),
        delay_sec=float(args.delay_sec),
    )

    if not csv_rows:
        print("No tier-3 pending recovery rows matched filters.")
        return 0

    for line in _build_summary_lines(csv_rows, mode=mode):
        print(line)

    if apply_counts is not None:
        print("DB apply results:")
        for key in ("promoted", "drop_inactive", "drop_deleted", "unchanged", "stale", "missing"):
            if apply_counts.get(key):
                print(f"  {key}: {apply_counts[key]}")

    output_path = args.output or _default_output_path()
    _write_csv(output_path, csv_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
