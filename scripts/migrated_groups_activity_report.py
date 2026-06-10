"""Report bot-persisted activity for supergroup-migrated support groups.

Reads affected groups from the pre-migration pg_dump (or --affected-csv), queries
live Postgres for bot-observable signals in the past N days, and writes:

- ``backups/migrated_groups_activity_summary_<ts>.csv`` — one row per migrated group
- ``backups/migrated_groups_active_users_<ts>.csv`` — per (group, user, activity_type)
- ``gc_active_migrated_invite_targets.csv`` (with ``--invite-targets-csv``) — DM-ready subset
  for ``scripts/dm_deposit_groups_invite.py``: active groups whose mapped player is not in
  the supergroup yet

Does not use Telethon; ordinary group chat messages are not logged by the bot.

Usage:
  python scripts/migrated_groups_activity_report.py
  python scripts/migrated_groups_activity_report.py --days 30 --only-active
  python scripts/migrated_groups_activity_report.py --club-key clubgto
  python scripts/migrated_groups_activity_report.py --affected-csv backups/affected_migrated_groups_*.csv
  python scripts/migrated_groups_activity_report.py --skip-membership-check
  python scripts/migrated_groups_activity_report.py --club-key round_table --invite-targets-csv gc_active_migrated_invite_targets.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger("migrated_groups_activity_report")

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from notification.chat_id import telegram_chat_id_variants  # noqa: E402
from scripts.backup_groups_reader import (  # noqa: E402
    AffectedMigratedGroup,
    find_earliest_upgrade_backup,
    resolve_affected_from_backup,
)


@dataclass(frozen=True)
class MigratedGroupRow:
    club_id: int
    club_key: str
    group_title: str
    old_chat_id: int
    current_chat_id: int


@dataclass
class GroupAgg:
    signals: set[str] = field(default_factory=set)
    last_activity_at: datetime | None = None
    payment_count: int = 0
    total_deposited_cents: int = 0
    active_user_ids: set[int] = field(default_factory=set)
    has_payment_activity: bool = False
    has_identifiable_user_activity: bool = False

    def touch(self, signal: str, at: datetime | None) -> None:
        self.signals.add(signal)
        self.last_activity_at = _max_ts(self.last_activity_at, at)

    def add_payment(self, amount_cents: int, at: datetime | None) -> None:
        self.payment_count += 1
        self.total_deposited_cents += int(amount_cents)
        self.has_payment_activity = True
        self.touch("payment", at)

    def add_user(self, user_id: int, signal: str, at: datetime | None) -> None:
        if user_id and int(user_id) > 0:
            self.active_user_ids.add(int(user_id))
            self.has_identifiable_user_activity = True
            self.touch(signal, at)


@dataclass(frozen=True)
class PlayerMembership:
    player_telegram_user_id: int | None
    in_group: str
    membership_status: str | None = None
    check_error: str | None = None


@dataclass
class UserAgg:
    first_at: datetime | None = None
    last_at: datetime | None = None
    event_count: int = 0

    def add(self, at: datetime | None) -> None:
        at = _as_utc(at)
        if at is None:
            return
        self.event_count += 1
        self.first_at = at if self.first_at is None else min(self.first_at, at)
        self.last_at = at if self.last_at is None else max(self.last_at, at)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _max_ts(current: datetime | None, new: datetime | None) -> datetime | None:
    new = _as_utc(new)
    if new is None:
        return current
    if current is None:
        return new
    return max(current, new)


def _in_window(dt: datetime | None, cutoff: datetime) -> bool:
    dt = _as_utc(dt)
    return dt is not None and dt >= cutoff


def _payment_ts(created_at: datetime | None, bound_at: datetime | None) -> datetime | None:
    return _as_utc(bound_at) or _as_utc(created_at)


def _stripe_ts(created_at: datetime | None, completed_at: datetime | None) -> datetime | None:
    return _max_ts(_as_utc(created_at), _as_utc(completed_at))


def _default_summary_csv() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _REPO_ROOT / "backups" / f"migrated_groups_activity_summary_{stamp}.csv"


def _default_users_csv() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _REPO_ROOT / "backups" / f"migrated_groups_active_users_{stamp}.csv"


def _default_invite_targets_csv() -> Path:
    return _REPO_ROOT / "gc_active_migrated_invite_targets.csv"


INVITE_TARGET_FIELDS = [
    "telegram_chat_id",
    "gc_title",
    "group_name",
    "support_group_chat_title",
    "club_id",
    "club_name",
    "club_key",
    "gg_player_id",
    "player_display_name",
    "player_telegram_user_id",
    "player_username",
    "active_in_past_30_days",
    "player_in_group",
    "player_membership_status",
    "last_activity_at",
    "payment_count_30d",
    "total_deposited_usd_30d",
    "activity_signals",
    "old_chat_id",
    "current_chat_id",
    "migration_status",
    "dm_sent",
    "dm_seq",
    "dm_sent_at",
]


def _configure_logging(*, quiet: bool) -> None:
    level = logging.WARNING if quiet else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
        force=True,
    )


def _log_groups_loaded(groups: list[MigratedGroupRow]) -> None:
    logger.info("Loaded %s migrated group(s) for activity report", len(groups))
    for i, group in enumerate(groups, start=1):
        logger.info(
            "[%s/%s] group club=%s title=%r old_chat_id=%s current_chat_id=%s",
            i,
            len(groups),
            group.club_key or group.club_id,
            group.group_title,
            group.old_chat_id,
            group.current_chat_id,
        )


def _load_affected_from_csv(path: Path) -> list[AffectedMigratedGroup]:
    if not path.is_file():
        raise SystemExit(f"Affected CSV not found: {path}")
    out: list[AffectedMigratedGroup] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                club_id = int(row["club_id"])
                old_chat_id = int(row["old_chat_id"])
            except (KeyError, TypeError, ValueError):
                continue
            current_raw = (row.get("current_chat_id") or "").strip()
            current_chat_id = int(current_raw) if current_raw else None
            out.append(
                AffectedMigratedGroup(
                    club_id=club_id,
                    title=(row.get("group_title") or "").strip(),
                    old_chat_id=old_chat_id,
                    current_chat_id=current_chat_id,
                    status=(row.get("status") or "migrated").strip(),
                )
            )
    return out


def _migrated_groups(
    affected: list[AffectedMigratedGroup],
    *,
    club_key_filter: str | None,
) -> list[MigratedGroupRow]:
    from club_gc_settings import CLUB_GC_CONFIG, get_club_gc_config_by_link_club_id

    club_id_filter: int | None = None
    if club_key_filter:
        cfg = CLUB_GC_CONFIG.get(club_key_filter)
        if cfg is None:
            raise SystemExit(f"Unknown club key: {club_key_filter}")
        club_id_filter = int(cfg.link_club_id)

    rows: list[MigratedGroupRow] = []
    for item in affected:
        if item.status != "migrated" or item.current_chat_id is None:
            continue
        if club_id_filter is not None and int(item.club_id) != club_id_filter:
            continue
        cfg = get_club_gc_config_by_link_club_id(int(item.club_id))
        rows.append(
            MigratedGroupRow(
                club_id=int(item.club_id),
                club_key=cfg.club_key if cfg else "",
                group_title=(item.title or "").strip(),
                old_chat_id=int(item.old_chat_id),
                current_chat_id=int(item.current_chat_id),
            )
        )
    return rows


def _build_chat_maps(
    groups: list[MigratedGroupRow],
) -> tuple[dict[int, int], dict[int, MigratedGroupRow]]:
    """Map any chat id variant -> canonical current_chat_id; current -> group row."""
    variant_to_current: dict[int, int] = {}
    groups_by_current: dict[int, MigratedGroupRow] = {}
    for group in groups:
        groups_by_current[group.current_chat_id] = group
        for cid in (group.old_chat_id, group.current_chat_id):
            for variant in telegram_chat_id_variants(cid):
                variant_to_current[int(variant)] = group.current_chat_id
    return variant_to_current, groups_by_current


def _resolve_current_chat_id(
    chat_id: int,
    variant_to_current: dict[int, int],
) -> int | None:
    for variant in telegram_chat_id_variants(chat_id):
        current = variant_to_current.get(int(variant))
        if current is not None:
            return current
    return None


def _load_player_identity_by_chat(
    chat_ids: set[int],
) -> dict[int, tuple[int | None, str | None, str | None]]:
    from bot.services.migration_group_readd import load_player_rows_by_chat

    return load_player_rows_by_chat(chat_ids)


def _player_in_group_status(member_status: str) -> bool:
    from telegram.constants import ChatMemberStatus as CMS

    return member_status in {
        CMS.MEMBER,
        CMS.ADMINISTRATOR,
        CMS.OWNER,
        CMS.RESTRICTED,
    }


async def _check_player_membership(
    groups: list[MigratedGroupRow],
    player_map: dict[int, tuple[int | None, str | None, str | None]],
    *,
    membership_delay: float,
) -> dict[int, PlayerMembership]:
    from telegram import Bot
    from telegram.error import BadRequest, RetryAfter, TimedOut

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN env var is required for membership checks")

    bot = Bot(token=token)
    out: dict[int, PlayerMembership] = {}
    try:
        for i, group in enumerate(groups, start=1):
            chat_id = int(group.current_chat_id)
            player_id, player_username, _club_key = player_map.get(
                chat_id, (None, None, None)
            )
            if player_id is None:
                out[chat_id] = PlayerMembership(
                    player_telegram_user_id=None,
                    in_group="no_player_id",
                )
                logger.info(
                    "[%s/%s] membership chat_id=%s title=%r player=unknown -> no_player_id",
                    i,
                    len(groups),
                    chat_id,
                    group.group_title,
                )
                continue

            status_value = "error"
            membership_status: str | None = None
            check_error: str | None = None
            for attempt in range(4):
                try:
                    member = await bot.get_chat_member(chat_id, int(player_id))
                    membership_status = str(member.status)
                    status_value = (
                        "yes" if _player_in_group_status(membership_status) else "no"
                    )
                    check_error = None
                    break
                except RetryAfter as exc:
                    wait_s = float(getattr(exc, "retry_after", 1) or 1) + 1.0
                    logger.warning(
                        "Rate limited checking chat_id=%s user_id=%s; sleeping %.1fs",
                        chat_id,
                        player_id,
                        wait_s,
                    )
                    await asyncio.sleep(wait_s)
                except TimedOut:
                    await asyncio.sleep(2.0**attempt)
                except BadRequest as exc:
                    check_error = type(exc).__name__
                    status_value = "error"
                    break
                except Exception as exc:
                    check_error = type(exc).__name__
                    status_value = "error"
                    break

            out[chat_id] = PlayerMembership(
                player_telegram_user_id=int(player_id),
                in_group=status_value,
                membership_status=membership_status,
                check_error=check_error,
            )
            status_detail = membership_status or ""
            if check_error:
                status_detail = (
                    f"{status_detail} error={check_error}".strip()
                    if status_detail
                    else f"error={check_error}"
                )
            logger.info(
                "[%s/%s] membership chat_id=%s title=%r player_id=%s username=%r "
                "in_group=%s status=%s",
                i,
                len(groups),
                chat_id,
                group.group_title,
                player_id,
                player_username,
                status_value,
                status_detail or "(none)",
            )
            if membership_delay > 0 and i < len(groups):
                await asyncio.sleep(membership_delay)
    finally:
        await bot.shutdown()

    in_group = sum(1 for m in out.values() if m.in_group == "yes")
    not_in_group = sum(1 for m in out.values() if m.in_group == "no")
    no_player = sum(1 for m in out.values() if m.in_group == "no_player_id")
    errors = sum(1 for m in out.values() if m.in_group == "error")
    logger.info(
        "Membership summary: in_group=%s not_in_group=%s no_player_id=%s error=%s",
        in_group,
        not_in_group,
        no_player,
        errors,
    )
    return out


def _user_key(
    current_chat_id: int,
    telegram_user_id: int,
    activity_type: str,
) -> tuple[int, int, str]:
    return (int(current_chat_id), int(telegram_user_id), activity_type)


def _record_user_event(
    user_aggs: dict[tuple[int, int, str], UserAgg],
    *,
    current_chat_id: int,
    telegram_user_id: int | None,
    activity_type: str,
    at: datetime | None,
    group_agg: GroupAgg,
    user_signal: str,
) -> None:
    if telegram_user_id is None:
        return
    uid = int(telegram_user_id)
    if uid <= 0:
        return
    group_agg.add_user(uid, user_signal, at)
    key = _user_key(current_chat_id, uid, activity_type)
    user_aggs.setdefault(key, UserAgg()).add(at)


def _collect_activity(
    groups: list[MigratedGroupRow],
    *,
    days: int,
) -> tuple[dict[int, GroupAgg], dict[tuple[int, int, str], UserAgg]]:
    from db.connection import get_db, init_engine
    from db.models import (
        CashAppPayment,
        CashierCashoutJob,
        CryptoPayment,
        GroupPaymentMethodBinding,
        PaymentMethodBindAttempt,
        PlayerActivity,
        StripeCheckoutSession,
        VenmoPayment,
        ZellePayment,
    )

    init_engine()
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(days))
    logger.info(
        "Querying bot activity for %s group(s) with cutoff >= %s",
        len(groups),
        cutoff.isoformat(),
    )
    variant_to_current, _groups_by_current = _build_chat_maps(groups)
    group_aggs: dict[int, GroupAgg] = {
        g.current_chat_id: GroupAgg() for g in groups
    }
    user_aggs: dict[tuple[int, int, str], UserAgg] = {}

    def resolve_group(chat_id: int) -> tuple[int | None, GroupAgg | None]:
        current = _resolve_current_chat_id(chat_id, variant_to_current)
        if current is None:
            return None, None
        return current, group_aggs.get(current)

    with get_db() as session:
        for row in (
            session.query(
                PlayerActivity.chat_id,
                PlayerActivity.telegram_user_id,
                PlayerActivity.activity_type,
                PlayerActivity.created_at,
            )
            .filter(PlayerActivity.cancelled.is_(False))
            .filter(PlayerActivity.created_at >= cutoff)
            .all()
        ):
            current, agg = resolve_group(int(row.chat_id))
            if current is None or agg is None:
                continue
            at = _as_utc(row.created_at)
            activity_type = (row.activity_type or "").strip() or "activity"
            agg.touch("player_activity", at)
            _record_user_event(
                user_aggs,
                current_chat_id=current,
                telegram_user_id=int(row.telegram_user_id),
                activity_type=activity_type,
                at=at,
                group_agg=agg,
                user_signal="player_activity",
            )

        for row in (
            session.query(
                StripeCheckoutSession.telegram_chat_id,
                StripeCheckoutSession.amount_cents,
                StripeCheckoutSession.created_at,
                StripeCheckoutSession.completed_at,
            )
            .filter(StripeCheckoutSession.status == "complete")
            .all()
        ):
            at = _stripe_ts(row.created_at, row.completed_at)
            if not _in_window(at, cutoff):
                continue
            current, agg = resolve_group(int(row.telegram_chat_id))
            if current is None or agg is None:
                continue
            agg.add_payment(int(row.amount_cents), at)
            agg.signals.add("stripe")

        payment_models = [
            (VenmoPayment, "venmo"),
            (CashAppPayment, "cashapp"),
            (ZellePayment, "zelle"),
            (CryptoPayment, "crypto"),
        ]
        for model, slug in payment_models:
            for row in (
                session.query(
                    model.telegram_chat_id,
                    model.amount_cents,
                    model.created_at,
                    model.bound_at,
                )
                .filter(model.telegram_chat_id.isnot(None))
                .filter(model.is_test.is_(False))
                .all()
            ):
                at = _payment_ts(row.created_at, row.bound_at)
                if not _in_window(at, cutoff):
                    continue
                current, agg = resolve_group(int(row.telegram_chat_id))
                if current is None or agg is None:
                    continue
                agg.add_payment(int(row.amount_cents), at)
                agg.signals.add(slug)

        for row in (
            session.query(
                CashierCashoutJob.chat_id,
                CashierCashoutJob.initiated_by,
                CashierCashoutJob.created_at,
            )
            .filter(CashierCashoutJob.created_at >= cutoff)
            .all()
        ):
            current, agg = resolve_group(int(row.chat_id))
            if current is None or agg is None:
                continue
            at = _as_utc(row.created_at)
            agg.touch("cashier", at)
            _record_user_event(
                user_aggs,
                current_chat_id=current,
                telegram_user_id=int(row.initiated_by),
                activity_type="cashout_job",
                at=at,
                group_agg=agg,
                user_signal="cashier",
            )

        for row in (
            session.query(
                PaymentMethodBindAttempt.telegram_chat_id,
                PaymentMethodBindAttempt.initiated_by_telegram_user_id,
                PaymentMethodBindAttempt.created_at,
                PaymentMethodBindAttempt.completed_at,
            )
            .filter(PaymentMethodBindAttempt.created_at >= cutoff)
            .all()
        ):
            current, agg = resolve_group(int(row.telegram_chat_id))
            if current is None or agg is None:
                continue
            at = _max_ts(_as_utc(row.created_at), _as_utc(row.completed_at))
            agg.touch("bind_attempt", at)
            _record_user_event(
                user_aggs,
                current_chat_id=current,
                telegram_user_id=row.initiated_by_telegram_user_id,
                activity_type="bind_attempt",
                at=at,
                group_agg=agg,
                user_signal="bind_attempt",
            )

        for row in (
            session.query(
                GroupPaymentMethodBinding.telegram_chat_id,
                GroupPaymentMethodBinding.bound_by_telegram_user_id,
                GroupPaymentMethodBinding.bound_at,
            )
            .filter(GroupPaymentMethodBinding.bound_at >= cutoff)
            .all()
        ):
            current, agg = resolve_group(int(row.telegram_chat_id))
            if current is None or agg is None:
                continue
            at = _as_utc(row.bound_at)
            agg.touch("method_bind", at)
            _record_user_event(
                user_aggs,
                current_chat_id=current,
                telegram_user_id=row.bound_by_telegram_user_id,
                activity_type="method_bind",
                at=at,
                group_agg=agg,
                user_signal="method_bind",
            )

    return group_aggs, user_aggs


def _format_ts(dt: datetime | None) -> str:
    dt = _as_utc(dt)
    if dt is None:
        return ""
    return dt.isoformat()


def _format_usd(cents: int) -> str:
    return f"{(Decimal(cents) / Decimal(100)):.2f}"


def _build_summary_rows(
    groups: list[MigratedGroupRow],
    group_aggs: dict[int, GroupAgg],
    *,
    days: int,
    player_map: dict[int, tuple[int | None, str | None, str | None]],
    membership_by_chat: dict[int, PlayerMembership],
    gg_player_by_chat: dict[int, str | None],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in groups:
        agg = group_aggs.get(group.current_chat_id, GroupAgg())
        signals = sorted(agg.signals)
        if agg.has_payment_activity and not agg.has_identifiable_user_activity:
            signals.append("group_payment_activity_no_user")
        active = bool(signals)
        player_id, username, _club_key = player_map.get(
            group.current_chat_id, (None, None, None)
        )
        membership = membership_by_chat.get(group.current_chat_id)
        rows.append(
            {
                "club_id": group.club_id,
                "club_key": group.club_key,
                "group_title": group.group_title,
                "old_chat_id": group.old_chat_id,
                "current_chat_id": group.current_chat_id,
                "gg_player_id": gg_player_by_chat.get(group.current_chat_id) or "",
                "player_telegram_user_id": player_id or "",
                "player_username": username or "",
                f"active_in_past_{days}_days": "yes" if active else "no",
                "player_in_group": membership.in_group if membership else "check_skipped",
                "player_membership_status": (membership.membership_status or "")
                if membership
                else "",
                "player_membership_error": (membership.check_error or "")
                if membership
                else "",
                "last_activity_at": _format_ts(agg.last_activity_at),
                "activity_signals": ",".join(signals),
                f"payment_count_{days}d": agg.payment_count,
                f"total_deposited_usd_{days}d": _format_usd(agg.total_deposited_cents),
                "active_user_count": len(agg.active_user_ids),
            }
        )
        logger.info(
            "summary chat_id=%s title=%r active=%s signals=%s player_in_group=%s",
            group.current_chat_id,
            group.group_title,
            "yes" if active else "no",
            ",".join(signals) or "(none)",
            membership.in_group if membership else "check_skipped",
        )
    return rows


def _is_invite_dm_target(row: dict[str, Any], *, days: int) -> bool:
    """Active migrated group with a known player who is not already in the supergroup."""
    active_key = f"active_in_past_{days}_days"
    if row.get(active_key) != "yes":
        return False
    player_raw = row.get("player_telegram_user_id")
    if player_raw in (None, ""):
        return False
    in_group = (row.get("player_in_group") or "").strip()
    if in_group == "yes":
        return False
    return in_group in {"no", "error"}


def _build_invite_target_rows(
    summary_rows: list[dict[str, Any]],
    *,
    days: int,
    club_name_by_id: dict[int, str],
) -> list[dict[str, Any]]:
    payment_count_key = f"payment_count_{days}d"
    total_usd_key = f"total_deposited_usd_{days}d"
    active_key = f"active_in_past_{days}_days"
    out: list[dict[str, Any]] = []
    for row in summary_rows:
        if not _is_invite_dm_target(row, days=days):
            continue
        title = (row.get("group_title") or "").strip()
        club_id = int(row["club_id"])
        current_chat_id = int(row["current_chat_id"])
        out.append(
            {
                "telegram_chat_id": current_chat_id,
                "gc_title": title,
                "group_name": title,
                "support_group_chat_title": title,
                "club_id": club_id,
                "club_name": club_name_by_id.get(club_id, ""),
                "club_key": row.get("club_key") or "",
                "gg_player_id": row.get("gg_player_id") or "",
                "player_display_name": title,
                "player_telegram_user_id": row.get("player_telegram_user_id") or "",
                "player_username": row.get("player_username") or "",
                "active_in_past_30_days": row.get(f"active_in_past_{days}_days") or "",
                "player_in_group": row.get("player_in_group") or "",
                "player_membership_status": row.get("player_membership_status") or "",
                "last_activity_at": row.get("last_activity_at") or "",
                "payment_count_30d": row.get(payment_count_key, 0),
                "total_deposited_usd_30d": row.get(total_usd_key, "0.00"),
                "activity_signals": row.get("activity_signals") or "",
                "old_chat_id": row.get("old_chat_id") or "",
                "current_chat_id": current_chat_id,
                "migration_status": "migrated",
                "dm_sent": "",
                "dm_seq": "",
                "dm_sent_at": "",
            }
        )
    out.sort(
        key=lambda r: (
            float(r.get("total_deposited_usd_30d") or 0),
            r.get("last_activity_at") or "",
        ),
        reverse=True,
    )
    return out


def _write_invite_targets_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=INVITE_TARGET_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Wrote %s invite DM target(s) to %s", len(rows), path)


def _build_user_rows(
    groups_by_current: dict[int, MigratedGroupRow],
    user_aggs: dict[tuple[int, int, str], UserAgg],
    player_map: dict[int, tuple[int | None, str | None, str | None]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (current_chat_id, telegram_user_id, activity_type), agg in sorted(
        user_aggs.items(),
        key=lambda item: (item[0][0], item[0][2], item[0][1]),
    ):
        group = groups_by_current.get(current_chat_id)
        if group is None:
            continue
        mapped_player_id, username, _club_key = player_map.get(
            current_chat_id, (None, None, None)
        )
        rows.append(
            {
                "club_id": group.club_id,
                "club_key": group.club_key,
                "group_title": group.group_title,
                "current_chat_id": current_chat_id,
                "telegram_user_id": telegram_user_id,
                "player_username": username or "",
                "is_mapped_player": "yes"
                if mapped_player_id is not None and int(mapped_player_id) == int(telegram_user_id)
                else "no",
                "activity_type": activity_type,
                "first_at": _format_ts(agg.first_at),
                "last_at": _format_ts(agg.last_at),
                "event_count": agg.event_count,
            }
        )
    return rows


def run_report(
    *,
    days: int,
    club_key_filter: str | None,
    only_active: bool,
    affected_csv: Path | None,
    backup_path: Path | None,
    summary_path: Path | None,
    users_path: Path | None,
    invite_targets_path: Path | None,
    check_membership: bool,
    membership_delay: float,
) -> dict[str, Any]:
    from api.payments_helpers import resolve_group_title
    from club_gc_settings import CLUB_GC_CONFIG
    from db.connection import get_db, init_engine
    from db.models import Club

    init_engine()
    if affected_csv is not None:
        logger.info("Loading affected groups from CSV: %s", affected_csv)
        affected = _load_affected_from_csv(affected_csv)
        backup_used = str(affected_csv)
    else:
        dump_path = (backup_path or find_earliest_upgrade_backup(_REPO_ROOT)).resolve()
        logger.info("Loading affected groups from backup: %s", dump_path)
        mtproto_club_ids = frozenset(
            int(cfg.link_club_id) for cfg in CLUB_GC_CONFIG.values()
        )
        affected = resolve_affected_from_backup(
            dump_path,
            mtproto_club_ids=mtproto_club_ids,
        )
        backup_used = str(dump_path)

    groups = _migrated_groups(affected, club_key_filter=club_key_filter)
    if not groups:
        raise SystemExit("No migrated groups matched filters.")
    _log_groups_loaded(groups)

    group_aggs, user_aggs = _collect_activity(groups, days=days)
    _, groups_by_current = _build_chat_maps(groups)

    chat_ids = {g.current_chat_id for g in groups}
    player_map = _load_player_identity_by_chat(chat_ids)
    with get_db() as session:
        club_name_by_id = {int(c.id): c.name for c in session.query(Club).all()}
        gg_player_by_chat: dict[int, str | None] = {}
        for chat_id in chat_ids:
            _, gg_player_id = resolve_group_title(session, chat_id)
            gg_player_by_chat[chat_id] = gg_player_id

    membership_by_chat: dict[int, PlayerMembership] = {}
    if check_membership:
        logger.info(
            "Checking mapped player membership via Bot API for %s group(s)",
            len(groups),
        )
        membership_by_chat = asyncio.run(
            _check_player_membership(
                groups,
                player_map,
                membership_delay=membership_delay,
            )
        )
    else:
        logger.info("Skipping player membership checks (--skip-membership-check)")

    summary_rows = _build_summary_rows(
        groups,
        group_aggs,
        days=days,
        player_map=player_map,
        membership_by_chat=membership_by_chat,
        gg_player_by_chat=gg_player_by_chat,
    )
    active_key = f"active_in_past_{days}_days"
    active_count = sum(1 for r in summary_rows if r.get(active_key) == "yes")
    inactive_count = len(summary_rows) - active_count

    invite_target_rows = _build_invite_target_rows(
        summary_rows,
        days=days,
        club_name_by_id=club_name_by_id,
    )

    user_rows = _build_user_rows(groups_by_current, user_aggs, player_map)
    if only_active:
        active_chat_ids = {
            int(r["current_chat_id"]) for r in summary_rows if r.get(active_key) == "yes"
        }
        summary_rows = [r for r in summary_rows if r.get(active_key) == "yes"]
        user_rows = [r for r in user_rows if int(r["current_chat_id"]) in active_chat_ids]

    out_summary = summary_path or _default_summary_csv()
    out_users = users_path or _default_users_csv()
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_users.parent.mkdir(parents=True, exist_ok=True)

    summary_fields = list(summary_rows[0].keys()) if summary_rows else [
        "club_id",
        "club_key",
        "group_title",
        "old_chat_id",
        "current_chat_id",
        "gg_player_id",
        "player_telegram_user_id",
        "player_username",
        f"active_in_past_{days}_days",
        "player_in_group",
        "player_membership_status",
        "player_membership_error",
        "last_activity_at",
        "activity_signals",
        f"payment_count_{days}d",
        f"total_deposited_usd_{days}d",
        "active_user_count",
    ]
    with out_summary.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=summary_fields)
        writer.writeheader()
        writer.writerows(summary_rows)

    user_fields = [
        "club_id",
        "club_key",
        "group_title",
        "current_chat_id",
        "telegram_user_id",
        "player_username",
        "is_mapped_player",
        "activity_type",
        "first_at",
        "last_at",
        "event_count",
    ]
    with out_users.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=user_fields)
        writer.writeheader()
        writer.writerows(user_rows)

    out_invite = invite_targets_path or _default_invite_targets_csv()
    _write_invite_targets_csv(out_invite, invite_target_rows)

    player_in_group = sum(
        1 for m in membership_by_chat.values() if m.in_group == "yes"
    )
    player_not_in_group = sum(
        1 for m in membership_by_chat.values() if m.in_group == "no"
    )

    return {
        "backup_or_affected_source": backup_used,
        "migrated_total": len(groups),
        "summary_rows": len(summary_rows),
        "active_count": active_count,
        "inactive_count": inactive_count,
        "user_rows": len(user_rows),
        "player_in_group": player_in_group,
        "player_not_in_group": player_not_in_group,
        "invite_target_count": len(invite_target_rows),
        "summary_path": str(out_summary),
        "users_path": str(out_users),
        "invite_targets_path": str(out_invite),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Lookback window in days (default: 30).",
    )
    parser.add_argument(
        "--only-active",
        action="store_true",
        help="Only include groups with bot-observable activity in the window.",
    )
    parser.add_argument(
        "--club-key",
        help="Limit to one MTProto club key (e.g. clubgto).",
    )
    parser.add_argument(
        "--affected-csv",
        type=Path,
        help="Use a pre-generated affected-groups CSV instead of parsing pg_dump.",
    )
    parser.add_argument(
        "--backup",
        type=Path,
        help="Pre-migration pg_dump (default: earliest backups/upgrade_supergroup_*/database.dump).",
    )
    parser.add_argument(
        "--summary-csv",
        type=Path,
        help="Output path for group summary CSV.",
    )
    parser.add_argument(
        "--users-csv",
        type=Path,
        help="Output path for per-user activity CSV.",
    )
    parser.add_argument(
        "--invite-targets-csv",
        type=Path,
        help=(
            "Output path for DM-ready invite targets "
            "(default: gc_active_migrated_invite_targets.csv)."
        ),
    )
    parser.add_argument(
        "--skip-membership-check",
        action="store_true",
        help="Skip Bot API getChatMember checks for mapped players.",
    )
    parser.add_argument(
        "--membership-delay",
        type=float,
        default=0.05,
        help="Seconds to sleep between membership checks (default: 0.05).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only warnings/errors on stderr.",
    )
    args = parser.parse_args()

    _configure_logging(quiet=bool(args.quiet))

    stats = run_report(
        days=int(args.days),
        club_key_filter=args.club_key,
        only_active=bool(args.only_active),
        affected_csv=args.affected_csv,
        backup_path=args.backup,
        summary_path=args.summary_csv,
        users_path=args.users_csv,
        invite_targets_path=args.invite_targets_csv,
        check_membership=not bool(args.skip_membership_check),
        membership_delay=float(args.membership_delay),
    )
    print(f"Source: {stats['backup_or_affected_source']}")
    print(
        f"Migrated groups: {stats['migrated_total']} | "
        f"active: {stats['active_count']} | "
        f"inactive: {stats['inactive_count']} | "
        f"user activity rows: {stats['user_rows']} | "
        f"player in group: {stats['player_in_group']} | "
        f"player not in group: {stats['player_not_in_group']}"
    )
    print(f"Summary: {stats['summary_path']}")
    print(f"Users: {stats['users_path']}")
    print(
        f"Invite DM targets: {stats['invite_target_count']} -> "
        f"{stats['invite_targets_path']}"
    )


if __name__ == "__main__":
    main()
