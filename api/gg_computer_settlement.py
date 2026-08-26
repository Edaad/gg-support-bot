"""Monday weekly settlement fetch from gg-computer week-data-rakebacks."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

import httpx
from sqlalchemy.orm import Session

from api.audit_ledger import LedgerEvent
from bot.services.gg_computer import gg_computer_base_url
from db.models import EarlyRakebackLine, EarlyRakebackSnapshot

MISSING_PLAYER_ID_LABEL = "(missing player id)"


class SettlementFetchError(Exception):
    pass


def is_monday_audit_date(club_slug: str, audit_date: date) -> bool:
    del club_slug  # audit_date is already club-local
    return audit_date.weekday() == 0


def settlement_week_bounds(audit_date: date) -> tuple[date, date]:
    """Mon–Sun week settled on this Monday (ends the day before)."""
    sunday = audit_date - timedelta(days=1)
    monday = sunday - timedelta(days=6)
    return monday, sunday


def settlement_week_dates(audit_date: date) -> list[date]:
    """Calendar days Mon–Sun of the week settled on audit_date (Monday)."""
    week_monday, week_sunday = settlement_week_bounds(audit_date)
    days: list[date] = []
    day = week_monday
    while day <= week_sunday:
        days.append(day)
        day += timedelta(days=1)
    return days


def sum_early_rakeback_by_player_for_week(
    session: Session,
    *,
    club_slug: str,
    week_monday: date,
    week_sunday: date,
) -> tuple[dict[str, Decimal], list[date]]:
    """Sum early RB USD by gg_player_id over [week_monday, week_sunday].

    Returns (totals, missing_snapshot_dates). Totals only include days that
    have an early_rakeback_snapshots row (aon-beta sync).
    """
    slug = club_slug.strip().lower()
    expected = []
    day = week_monday
    while day <= week_sunday:
        expected.append(day)
        day += timedelta(days=1)

    snapshots = (
        session.query(EarlyRakebackSnapshot)
        .filter(
            EarlyRakebackSnapshot.club_slug == slug,
            EarlyRakebackSnapshot.audit_date >= week_monday,
            EarlyRakebackSnapshot.audit_date <= week_sunday,
        )
        .all()
    )
    present = {s.audit_date for s in snapshots}
    missing = [d for d in expected if d not in present]
    if not snapshots:
        return {}, missing

    snap_ids = [int(s.id) for s in snapshots]
    lines = (
        session.query(EarlyRakebackLine)
        .filter(EarlyRakebackLine.snapshot_id.in_(snap_ids))
        .all()
    )
    totals: dict[str, Decimal] = {}
    for line in lines:
        gid = (line.gg_player_id or "").strip()
        if not gid:
            continue
        totals[gid] = totals.get(gid, Decimal(0)) + Decimal(str(line.amount_usd))
    return totals, missing


def net_settlement_events_after_early_rb(
    events: list[LedgerEvent],
    early_by_player: dict[str, Decimal],
) -> tuple[list[LedgerEvent], list[str]]:
    """monday_settlement = max(0, weekly_rakeback − early_rb_that_week).

    Events without gg_player_id are left unchanged (cannot attribute early RB).
    Zero remainders are dropped.
    """
    warnings: list[str] = []
    out: list[LedgerEvent] = []
    for event in events:
        if event.source != "monday_settlement":
            out.append(event)
            continue
        gid = (event.gg_player_id or "").strip()
        if not gid:
            warnings.append(
                "Monday settlement row missing gg_id; cannot net early RB"
                + (f" (detail={event.detail!r})" if event.detail else "")
            )
            out.append(event)
            continue
        early = early_by_player.get(gid, Decimal(0))
        remaining = event.amount_usd - early
        if remaining <= 0:
            continue
        if early > 0:
            out.append(replace(event, amount_usd=remaining))
        else:
            out.append(event)
    return out, warnings


@dataclass
class _AggRow:
    amount: Decimal = Decimal(0)
    nickname: str = ""
    count: int = 0
    blank_id: bool = False


def _aggregate_week_data_entries(
    entries: list[object],
) -> tuple[dict[str, _AggRow], list[str]]:
    """Sum by playerId (or noid key); skip non-positive amounts. Returns (by_key, warnings)."""
    warnings: list[str] = []
    by_key: dict[str, _AggRow] = {}
    noid_seq = 0

    for raw in entries:
        if not isinstance(raw, dict):
            continue
        amount_raw = raw.get("rakebackAmount")
        if amount_raw is None:
            continue
        amount = Decimal(str(amount_raw))
        if amount <= 0:
            continue

        player_id = raw.get("playerId")
        gg_id = (str(player_id).strip() if player_id is not None else "") or ""
        nickname = (raw.get("nickname") or "")
        if not isinstance(nickname, str):
            nickname = str(nickname) if nickname is not None else ""
        nickname = nickname.strip()

        if not gg_id:
            key = f"noid:{noid_seq}:{nickname or 'unknown'}"
            noid_seq += 1
            row = _AggRow(amount=amount, nickname=nickname, count=1, blank_id=True)
            by_key[key] = row
            warnings.append(
                "Monday settlement row missing playerId"
                + (f" (nickname={nickname!r})" if nickname else "")
            )
            continue

        existing = by_key.get(gg_id)
        if existing is None:
            by_key[gg_id] = _AggRow(
                amount=amount,
                nickname=nickname,
                count=1,
                blank_id=False,
            )
        else:
            existing.amount += amount
            existing.count += 1
            if not existing.nickname and nickname:
                existing.nickname = nickname

    for gid, row in by_key.items():
        if not row.blank_id and row.count > 1:
            warnings.append(
                f"Monday settlement duplicate playerId={gid!r} "
                f"({row.count} entries); amounts summed"
            )

    return by_key, warnings


def fetch_settlement_events(
    *,
    club_slug: str,
    audit_date: date,
    timeout: float = 60.0,
) -> tuple[list[LedgerEvent], list[str]]:
    """Fetch Monday settlement rakeback as ledger events. Empty if not Monday.

    Amounts are gross weekly rakeback from gg-computer week-data-rakebacks.
    Callers should net early RB for the settled week via
    net_settlement_events_after_early_rb.
    """
    warnings: list[str] = []
    if not is_monday_audit_date(club_slug, audit_date):
        return [], warnings

    base = gg_computer_base_url()
    if not base:
        raise SettlementFetchError(
            "GG_COMPUTER_BASE_URL is not configured; cannot fetch Monday settlement"
        )

    slug = club_slug.strip().lower()
    week_monday, week_sunday = settlement_week_bounds(audit_date)
    start_s = week_monday.isoformat()
    end_s = week_sunday.isoformat()

    try:
        with httpx.Client(timeout=timeout) as client:
            res = client.get(
                f"{base}/week-data-rakebacks/{slug}",
                params={"startDate": start_s, "endDate": end_s},
            )
            res.raise_for_status()
            body = res.json()
    except httpx.HTTPError as exc:
        raise SettlementFetchError(f"gg-computer settlement fetch failed: {exc}") from exc

    if not isinstance(body, dict):
        raise SettlementFetchError("Invalid gg-computer /week-data-rakebacks response")

    entries = body.get("entries")
    if not isinstance(entries, list):
        raise SettlementFetchError(
            "Invalid gg-computer /week-data-rakebacks response (entries)"
        )
    if not entries:
        raise SettlementFetchError(
            f"No week-data-rakebacks for club {slug!r} "
            f"startDate={start_s} endDate={end_s}"
        )

    week_data_count = body.get("weekDataCount")
    if isinstance(week_data_count, int) and week_data_count > 1:
        warnings.append(
            f"Monday settlement weekDataCount={week_data_count} for {slug!r} "
            f"{start_s}..{end_s}; duplicate playerIds will be summed"
        )

    by_key, agg_warnings = _aggregate_week_data_entries(entries)
    warnings.extend(agg_warnings)

    events: list[LedgerEvent] = []
    for key, row in by_key.items():
        if row.amount <= 0:
            continue
        if row.blank_id:
            nick_part = row.nickname or "unknown"
            events.append(
                LedgerEvent(
                    source="monday_settlement",
                    gg_player_id=None,
                    amount_usd=row.amount,
                    occurred_at_utc=None,
                    external_id=f"monday:{start_s}:{end_s}:noid:{nick_part}",
                    detail=row.nickname or None,
                    display_name=MISSING_PLAYER_ID_LABEL,
                )
            )
            continue
        events.append(
            LedgerEvent(
                source="monday_settlement",
                gg_player_id=key,
                amount_usd=row.amount,
                occurred_at_utc=None,
                external_id=f"monday:{start_s}:{end_s}:{key}",
                detail=row.nickname or None,
            )
        )
    return events, warnings


def fetch_netted_settlement_events(
    session: Session,
    *,
    club_slug: str,
    audit_date: date,
    timeout: float = 60.0,
) -> tuple[list[LedgerEvent], list[str]]:
    """Fetch Monday settlement and subtract that week's early RB (aon-beta snapshots)."""
    events, warnings = fetch_settlement_events(
        club_slug=club_slug, audit_date=audit_date, timeout=timeout
    )
    if not events:
        return events, warnings

    week_monday, week_sunday = settlement_week_bounds(audit_date)
    early_by_player, missing_days = sum_early_rakeback_by_player_for_week(
        session,
        club_slug=club_slug,
        week_monday=week_monday,
        week_sunday=week_sunday,
    )
    if missing_days:
        missing_s = ", ".join(d.isoformat() for d in missing_days)
        warnings.append(
            f"Missing early RB snapshots for {club_slug.strip().lower()} "
            f"on {missing_s}; Monday settlement early-RB net may be high"
        )
    netted, net_warnings = net_settlement_events_after_early_rb(
        events, early_by_player
    )
    warnings.extend(net_warnings)
    return netted, warnings
