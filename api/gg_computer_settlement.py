"""Monday weekly settlement fetch from gg-computer weekly_profits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx

from api.audit_ledger import LedgerEvent
from bot.services.gg_computer import gg_computer_base_url


class SettlementFetchError(Exception):
    pass


@dataclass(frozen=True)
class SettlementPlayerRow:
    gg_id: str
    rakeback: Decimal


def is_monday_audit_date(club_slug: str, audit_date: date) -> bool:
    local = audit_date
    return local.weekday() == 0


def _settlement_sunday(audit_date: date) -> str:
    """Prior Sunday (week ending day before Monday settlement)."""
    return (audit_date - timedelta(days=1)).isoformat()


def fetch_settlement_events(
    *,
    club_slug: str,
    audit_date: date,
    timeout: float = 60.0,
) -> tuple[list[LedgerEvent], list[str]]:
    """Fetch Monday settlement rakeback as ledger events. Empty if not Monday."""
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
