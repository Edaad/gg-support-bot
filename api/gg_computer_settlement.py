"""Monday weekly settlement fetch from gg-computer weekly_profits."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

import httpx
from sqlalchemy.orm import Session

from api.audit_ledger import LedgerEvent
from bot.services.gg_computer import gg_computer_base_url
from db.models import EarlyRakebackLine, EarlyRakebackSnapshot


class SettlementFetchError(Exception):
    pass


@dataclass(frozen=True)
class SettlementPlayerRow:
    gg_id: str
    rakeback: Decimal


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


def _settlement_sunday(audit_date: date) -> str:
    """Prior Sunday (week ending day before Monday settlement)."""
    return (audit_date - timedelta(days=1)).isoformat()


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


def fetch_settlement_events(
    *,
    club_slug: str,
    audit_date: date,
    timeout: float = 60.0,
) -> tuple[list[LedgerEvent], list[str]]:
    """Fetch Monday settlement rakeback as ledger events. Empty if not Monday.

    Amounts are gross weekly rakeback from gg-computer. Callers should net
    early RB for the settled week via net_settlement_events_after_early_rb.
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
    sunday = _settlement_sunday(audit_date)

    try:
        with httpx.Client(timeout=timeout) as client:
            weeks_res = client.get(
                f"{base}/processed-weeks",
                params={"clubId": slug, "from": sunday, "to": sunday},
            )
            weeks_res.raise_for_status()
            weeks = weeks_res.json()
            if not isinstance(weeks, list) or not weeks:
                raise SettlementFetchError(
                    f"No processed week ending {sunday} for club {slug!r}"
                )

            week_id = weeks[0].get("weekId")
            if not week_id:
                raise SettlementFetchError(
                    f"Processed week missing weekId for club {slug!r}"
                )

            players_res = client.get(
                f"{base}/players",
                params={
                    "clubId": slug,
                    "weekId": str(week_id),
                    "pageSize": 5000,
                },
            )
            players_res.raise_for_status()
            body = players_res.json()
    except httpx.HTTPError as exc:
        raise SettlementFetchError(f"gg-computer settlement fetch failed: {exc}") from exc

    players = body.get("players") if isinstance(body, dict) else None
    if not isinstance(players, list):
        raise SettlementFetchError("Invalid gg-computer /players response")

    events: list[LedgerEvent] = []
    for row in players:
        if not isinstance(row, dict):
            continue
        gg_id = (row.get("gg_id") or "").strip()
        rakeback = row.get("rakeback")
        if rakeback is None:
            continue
        amount = Decimal(str(rakeback))
        if amount == 0:
            continue
        if not gg_id:
            nickname = (row.get("nickname") or "").strip()
            warnings.append(
                f"Monday settlement row missing gg_id"
                + (f" (nickname={nickname!r})" if nickname else "")
            )
            events.append(
                LedgerEvent(
                    source="monday_settlement",
                    gg_player_id=None,
                    amount_usd=amount,
                    occurred_at_utc=None,
                    external_id=f"monday:{week_id}:{nickname or 'unknown'}",
                    detail=nickname or None,
                )
            )
            continue
        events.append(
            LedgerEvent(
                source="monday_settlement",
                gg_player_id=gg_id,
                amount_usd=amount,
                occurred_at_utc=None,
                external_id=f"monday:{week_id}:{gg_id}",
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
