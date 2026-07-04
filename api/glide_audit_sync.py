"""Glide RT Hub fetch + dedupe for audit reconcile."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from api.audit_ledger import LedgerEvent
from api.club_audit_timezone import audit_day_window_utc, occurred_at_in_audit_day


class GlideConfigError(Exception):
    pass


def audit_glide_required() -> bool:
    raw = os.getenv("AUDIT_GLIDE_REQUIRED", "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


def glide_audit_configured() -> bool:
    token = (os.getenv("GLIDE_API_TOKEN") or "").strip()
    table_id = (os.getenv("GLIDE_AUDIT_TABLE_ID") or "").strip()
    return bool(token and table_id)


def _glide_column(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip()


def _parse_glide_timestamp(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        text = str(raw).strip()
        if not text:
            return None
        text = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fetch_glide_rows(*, timeout: float = 120.0) -> list[dict[str, Any]]:
    token = (os.getenv("GLIDE_API_TOKEN") or "").strip()
    table_id = (os.getenv("GLIDE_AUDIT_TABLE_ID") or "").strip()
    if not token or not table_id:
        return []

    base_url = (os.getenv("GLIDE_API_BASE_URL") or "https://api.glideapps.com").rstrip(
        "/"
    )
    rows: list[dict[str, Any]] = []
    continuation: str | None = None

    with httpx.Client(timeout=timeout) as client:
        while True:
            params: dict[str, str | int] = {"limit": 500}
            if continuation:
                params["continuation"] = continuation
            res = client.get(
                f"{base_url}/tables/{table_id}/rows",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                params=params,
            )
            res.raise_for_status()
            body = res.json()
            batch = body.get("data") if isinstance(body, dict) else None
            if isinstance(batch, list):
                rows.extend([r for r in batch if isinstance(r, dict)])
            continuation = body.get("continuation") if isinstance(body, dict) else None
            if not continuation:
                break
    return rows


def _row_to_event(
    row: dict[str, Any],
    *,
    club_slug: str,
    col_club: str,
    col_player: str,
    col_amount: str,
    col_timestamp: str,
    col_type: str,
) -> LedgerEvent | None:
    row_club = str(row.get(col_club) or "").strip().lower()
    if row_club and row_club != club_slug.strip().lower():
        return None
    gg_id = str(row.get(col_player) or "").strip() or None
    amount_raw = row.get(col_amount)
    if amount_raw is None:
        return None
    amount = Decimal(str(amount_raw))
    if amount == 0:
        return None
    event_type = str(row.get(col_type) or "").strip().lower()
    if event_type in ("cashout", "withdrawal", "remove"):
        amount = -abs(amount)
    else:
        amount = abs(amount)
    occurred_at = _parse_glide_timestamp(row.get(col_timestamp))
    row_id = row.get("$rowID") or row.get("$rowId") or row.get("rowID") or "unknown"
    return LedgerEvent(
        source="glide",
        gg_player_id=gg_id,
        amount_usd=amount,
        occurred_at_utc=occurred_at,
        external_id=f"glide:{row_id}",
    )


def dedupe_glide_events(
    glide_events: list[LedgerEvent],
    existing_events: list[LedgerEvent],
) -> list[LedgerEvent]:
    """Drop Glide rows that match an existing Postgres ledger event."""
    existing_keys: set[tuple[str | None, Decimal]] = set()
    for event in existing_events:
        if event.source == "glide":
            continue
        existing_keys.add((event.gg_player_id, event.amount_usd))
        if event.source == "cashout":
            existing_keys.add((event.gg_player_id, -event.amount_usd))

    deduped: list[LedgerEvent] = []
    for event in glide_events:
        key = (event.gg_player_id, event.amount_usd)
        neg_key = (event.gg_player_id, -event.amount_usd)
        if key in existing_keys or neg_key in existing_keys:
            continue
        deduped.append(event)
    return deduped


def fetch_glide_ledger_events(
    *,
    club_slug: str,
    audit_date: date,
    existing_events: list[LedgerEvent],
) -> tuple[list[LedgerEvent], list[str]]:
    """Fetch Glide events for audit day, deduped against Postgres ledger."""
    warnings: list[str] = []
    if not glide_audit_configured():
        if audit_glide_required():
            raise GlideConfigError(
                "AUDIT_GLIDE_REQUIRED is set but GLIDE_API_TOKEN or "
                "GLIDE_AUDIT_TABLE_ID is missing"
            )
        warnings.append("Glide audit table not configured; glide_net = 0")
        return [], warnings

    col_club = _glide_column("GLIDE_AUDIT_COL_CLUB", "club")
    col_player = _glide_column("GLIDE_AUDIT_COL_PLAYER", "gg_player_id")
    col_amount = _glide_column("GLIDE_AUDIT_COL_AMOUNT", "amount")
    col_timestamp = _glide_column("GLIDE_AUDIT_COL_TIMESTAMP", "timestamp")
    col_type = _glide_column("GLIDE_AUDIT_COL_TYPE", "event_type")

    try:
        raw_rows = _fetch_glide_rows()
    except httpx.HTTPError as exc:
        if audit_glide_required():
            raise GlideConfigError(f"Glide fetch failed: {exc}") from exc
        warnings.append(f"Glide fetch failed: {exc}")
        return [], warnings

    slug = club_slug.strip().lower()
    parsed: list[LedgerEvent] = []
    for row in raw_rows:
        event = _row_to_event(
            row,
            club_slug=slug,
            col_club=col_club,
            col_player=col_player,
            col_amount=col_amount,
            col_timestamp=col_timestamp,
            col_type=col_type,
        )
        if event is None:
            continue
        if event.occurred_at_utc and not occurred_at_in_audit_day(
            event.occurred_at_utc, slug, audit_date
        ):
            continue
        parsed.append(event)

    return dedupe_glide_events(parsed, existing_events), warnings
