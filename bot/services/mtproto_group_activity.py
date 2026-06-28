"""Telethon helpers: last non-support message activity and legacy merge."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DialogActivitySnapshot:
    title: str
    chat_id: int
    kind: str
    last_message_at: datetime | None
    activity_basis: str
    duplicate_title: bool = False
    newer_same_title_chat_id: int | None = None


@dataclass(frozen=True)
class ExternalActivityResult:
    last_external_message_at: datetime | None
    activity_basis: str


@dataclass(frozen=True)
class MergedExternalActivity:
    last_external_message_at: datetime | None
    activity_basis: str
    last_external_supergroup_at: datetime | None
    activity_basis_supergroup: str
    last_external_legacy_at: datetime | None
    activity_basis_legacy: str
    activity_merged_from: str


def utc_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_inactive_flags(
    last_external_message_at: datetime | None,
    *,
    now: datetime,
) -> tuple[bool, bool]:
    """Return (inactive_90d, inactive_180d) from merged last external message time."""
    last = utc_dt(last_external_message_at)
    if last is None:
        return True, True
    days = max(0, (now - last).days)
    return days >= 90, days >= 180


def message_sender_id(message: Any) -> int | None:
    sender_id = getattr(message, "sender_id", None)
    if sender_id is not None:
        return int(sender_id)
    from_id = getattr(message, "from_id", None)
    if from_id is None:
        return None
    from telethon.utils import get_peer_id

    try:
        return int(get_peer_id(from_id))
    except Exception:
        return None


async def resolve_exclude_user_ids(client, cfg, me_id: int) -> frozenset[int]:
    """User ids whose messages should not count as external activity."""

    from club_gc_settings import get_gc_users_to_add

    exclude: set[int] = {int(me_id)}
    markers = list(get_gc_users_to_add(cfg))
    bot_account = (cfg.bot_account or "").strip()
    if bot_account:
        markers.append(bot_account)

    for marker in markers:
        lookup = marker.strip()
        if not lookup:
            continue
        if not lookup.startswith("@") and not lookup.lstrip("-").isdigit():
            lookup = f"@{lookup.lstrip('@')}"
        try:
            ent = await client.get_entity(lookup)
            exclude.add(int(ent.id))
        except Exception as exc:
            logger.warning(
                "Could not resolve exclude marker %s: %s",
                marker,
                type(exc).__name__,
            )

    return frozenset(exclude)


async def last_eligible_player_message_at(
    client,
    entity,
    cfg,
    *,
    self_id: int | None,
    history_limit: int,
) -> ExternalActivityResult:
    """Return last message from an eligible player (not staff/bot/support)."""

    from bot.services.mtproto_group_player import (
        _eligible_player_filter_context,
        is_eligible_player_user,
    )

    invite_ids, invite_usernames, skip_operators, skip_dashboard_admins = (
        await _eligible_player_filter_context(client, cfg, self_id=self_id)
    )

    async def sender_is_eligible(msg: Any) -> bool:
        if getattr(msg, "out", False):
            return False
        if not getattr(msg, "sender_id", None):
            return False
        try:
            sender = await msg.get_sender()
        except Exception:
            return False
        return is_eligible_player_user(
            sender,
            self_id=self_id,
            invite_ids=invite_ids,
            invite_usernames=invite_usernames,
            skip_operators=skip_operators,
            skip_dashboard_admins=skip_dashboard_admins,
        )

    latest = await client.get_messages(entity, limit=1)
    if not latest:
        return ExternalActivityResult(None, "empty")

    msg = latest[0]
    if await sender_is_eligible(msg):
        return ExternalActivityResult(utc_dt(msg.date), "external")

    async for msg in client.iter_messages(entity, limit=history_limit):
        if await sender_is_eligible(msg):
            return ExternalActivityResult(utc_dt(msg.date), "external")

    return ExternalActivityResult(None, "support_only")


async def last_external_message_at(
    client,
    entity,
    *,
    exclude_user_ids: frozenset[int],
    history_limit: int,
) -> ExternalActivityResult:
    """Return last message time from a non-excluded sender, plus activity basis."""

    latest = await client.get_messages(entity, limit=1)
    if not latest:
        return ExternalActivityResult(None, "empty")

    msg = latest[0]
    sender_id = message_sender_id(msg)
    if sender_id is not None and sender_id not in exclude_user_ids:
        return ExternalActivityResult(utc_dt(msg.date), "external")

    async for msg in client.iter_messages(entity, limit=history_limit):
        sender_id = message_sender_id(msg)
        if sender_id is None:
            continue
        if sender_id not in exclude_user_ids:
            return ExternalActivityResult(utc_dt(msg.date), "external")

    return ExternalActivityResult(None, "support_only")


def merge_external_activity(
    supergroup: ExternalActivityResult,
    legacy: ExternalActivityResult | None,
) -> MergedExternalActivity:
    """Merge supergroup + legacy scans; take the most recent external timestamp."""

    sg_ts = utc_dt(supergroup.last_external_message_at)
    leg_ts = utc_dt(legacy.last_external_message_at) if legacy is not None else None
    leg_basis = legacy.activity_basis if legacy is not None else "none"

    if sg_ts is None and leg_ts is None:
        merged_from = "none"
        if supergroup.activity_basis == "empty" and leg_basis in ("empty", "none"):
            merged_basis = "empty"
        elif supergroup.activity_basis == "support_only" or leg_basis == "support_only":
            merged_basis = "support_only"
        else:
            merged_basis = supergroup.activity_basis if leg_basis == "none" else leg_basis
        return MergedExternalActivity(
            None,
            merged_basis,
            sg_ts,
            supergroup.activity_basis,
            leg_ts,
            leg_basis,
            merged_from,
        )

    if sg_ts is not None and leg_ts is not None:
        if sg_ts >= leg_ts:
            merged_from = "both" if sg_ts != leg_ts else "supergroup"
            winner_ts, winner_basis = sg_ts, supergroup.activity_basis
        else:
            merged_from = "both"
            winner_ts, winner_basis = leg_ts, legacy.activity_basis  # type: ignore[union-attr]
        return MergedExternalActivity(
            winner_ts,
            winner_basis,
            sg_ts,
            supergroup.activity_basis,
            leg_ts,
            leg_basis,
            merged_from,
        )

    if sg_ts is not None:
        return MergedExternalActivity(
            sg_ts,
            supergroup.activity_basis,
            sg_ts,
            supergroup.activity_basis,
            leg_ts,
            leg_basis,
            "supergroup",
        )

    return MergedExternalActivity(
        leg_ts,
        legacy.activity_basis if legacy is not None else leg_basis,  # type: ignore[union-attr]
        sg_ts,
        supergroup.activity_basis,
        leg_ts,
        leg_basis,
        "legacy",
    )


def merge_external_activity_results(
    results: list[ExternalActivityResult],
) -> ExternalActivityResult:
    """Merge multiple chat scans; keep the newest eligible-player activity timestamp."""

    if not results:
        return ExternalActivityResult(None, "none")

    best_ts: datetime | None = None
    best_basis = "none"
    for result in results:
        ts = utc_dt(result.last_external_message_at)
        if ts is not None and (best_ts is None or ts >= best_ts):
            best_ts = ts
            best_basis = result.activity_basis

    if best_ts is not None:
        return ExternalActivityResult(best_ts, best_basis)

    bases = {r.activity_basis for r in results}
    if bases <= {"empty", "none"}:
        return ExternalActivityResult(None, "empty")
    if "support_only" in bases:
        return ExternalActivityResult(None, "support_only")
    if "entity_gone" in bases:
        return ExternalActivityResult(None, "entity_gone")
    return ExternalActivityResult(None, "support_only")


def resolve_legacy_chat_ids(
    *,
    telegram_chat_id: int,
    group_title: str,
    club_id: int | None,
    basic_groups_by_title: dict[str, list[int]] | dict[str, int] | None = None,
) -> list[int]:
    """All known pre-migration basic group ids for a supergroup outreach row."""

    from bot.services.chat_id_remap import find_all_legacy_group_chat_ids
    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    supergroup_id = int(telegram_chat_id)
    ids: set[int] = set()

    with get_db() as session:
        rows = (
            session.query(MigratedGroupRecovery.old_chat_id)
            .filter(MigratedGroupRecovery.telegram_chat_id == supergroup_id)
            .all()
        )
    for (old_id,) in rows:
        if old_id is None:
            continue
        cid = int(old_id)
        if cid != supergroup_id:
            ids.add(cid)

    for cid in find_all_legacy_group_chat_ids(
        new_chat_id=supergroup_id,
        title=group_title,
        club_id=club_id,
    ):
        ids.add(int(cid))

    if basic_groups_by_title:
        key = group_title.casefold()
        raw = basic_groups_by_title.get(key)
        if isinstance(raw, int):
            candidates = [raw]
        elif raw:
            candidates = list(raw)
        else:
            candidates = []
        for cid in candidates:
            if int(cid) != supergroup_id:
                ids.add(int(cid))

    return sorted(ids)


def resolve_legacy_chat_id(
    *,
    telegram_chat_id: int,
    group_title: str,
    club_id: int | None,
    basic_groups_by_title: dict[str, list[int]] | dict[str, int] | None = None,
) -> int | None:
    """Primary legacy basic group id (first resolved; compat)."""
    ids = resolve_legacy_chat_ids(
        telegram_chat_id=telegram_chat_id,
        group_title=group_title,
        club_id=club_id,
        basic_groups_by_title=basic_groups_by_title,
    )
    return ids[0] if ids else None


def annotate_duplicate_titles(
    rows: list[DialogActivitySnapshot],
) -> list[DialogActivitySnapshot]:
    """Flag stale dialogs that share a title with a newer chat (post-migration duplicate)."""

    by_title: dict[str, list[DialogActivitySnapshot]] = {}
    for row in rows:
        by_title.setdefault(row.title.casefold(), []).append(row)

    out: list[DialogActivitySnapshot] = []
    for row in rows:
        peers = by_title.get(row.title.casefold(), [row])
        if len(peers) < 2:
            out.append(row)
            continue

        newer = max(
            (peer for peer in peers if peer.chat_id != row.chat_id),
            key=lambda peer: peer.last_message_at
            or datetime.min.replace(tzinfo=timezone.utc),
            default=None,
        )
        if newer is None:
            out.append(row)
            continue

        row_last = row.last_message_at or datetime.min.replace(tzinfo=timezone.utc)
        newer_last = newer.last_message_at or datetime.min.replace(tzinfo=timezone.utc)
        if newer_last <= row_last:
            out.append(row)
            continue

        out.append(
            DialogActivitySnapshot(
                title=row.title,
                chat_id=row.chat_id,
                kind=row.kind,
                last_message_at=row.last_message_at,
                activity_basis=row.activity_basis,
                duplicate_title=True,
                newer_same_title_chat_id=newer.chat_id,
            )
        )
    return out
