"""Outbound sends (money leaving Venmo/Cash App/crypto) and Round Table balances."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from mcp.server.mcpserver import MCPServer

from api.account_balances import (
    METHODS as BALANCE_METHODS,
)
from api.account_balances import (
    WINDOW,
    list_account_balances,
)
from api.account_balances import (
    list_blocking_sends as _list_blocking_sends,
)
from api.outbound_sends import list_outbound_sends as _list_outbound_sends
from db.models import OutboundSend
from mcp_server.dates import parse_range
from mcp_server.db import read_session
from mcp_server.envelope import page
from mcp_server.errors import bad_input, translate_errors
from mcp_server.serialize import cents_to_usd
from mcp_server.tools._common import READ_ONLY, clamp_limit, clamp_offset

_WINDOW_HOURS = int(WINDOW.total_seconds() // 3600)


def _send_to_dict(row: OutboundSend) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "method": row.method,
        "tag": row.tag,
        "method_owner": row.method_owner,
        "recipient": row.recipient,
        "amount_cents": int(row.amount_cents),
        "amount_usd": cents_to_usd(row.amount_cents),
        "tag_matched": bool(row.tag_matched),
        "source_external_id": row.source_external_id,
        "paid_at": row.paid_at,
        "created_at": row.created_at,
    }


@translate_errors
def list_outbound_sends(
    method: str | None = None,
    tag: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    limit: int = 50,
    offset: int = 0,
    include_pii: bool = False,
) -> dict[str, Any]:
    """Amounts that left a sending account (Zapier-ingested or admin-entered),
    newest first. `method` is venmo|cashapp|crypto; `tag` is the sending handle
    (@venmo, $cashapp). `from_`/`to` filter created_at (UTC datetimes; YYYY-MM-DD
    means that whole America/New_York day). Amounts are amount_cents (+ amount_usd).
    `recipient` is masked unless include_pii. See get_account_balances for totals."""
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    start, stop = parse_range(from_, to)
    if stop is not None:
        # The helper filters created_at <= to_dt; make the exclusive end inclusive.
        stop = stop - timedelta(microseconds=1)
    with read_session() as session:
        rows, total = _list_outbound_sends(
            session,
            method=method,
            tag=tag,
            from_dt=start,
            to_dt=stop,
            limit=limit,
            offset=offset,
        )
        items = [_send_to_dict(row) for row in rows]
    return page(
        items,
        total=total,
        limit=limit,
        offset=offset,
        include_pii=include_pii,
        notes=["amounts are amount_cents (amount_usd derived)"],
    )


@translate_errors
def get_account_balances() -> dict[str, Any]:
    """Round Table Venmo and Cash App balance cards: running balance (offset +
    received - sent), this ET week's inflow vs its cap, and remaining sending
    power inside the rolling 168-hour window. All amounts are cents (``*_cents``)
    with ``*_usd`` twins. Use list_blocking_sends for the sends eating the window."""
    with read_session() as session:
        grouped = list_account_balances(session)
    items: list[dict[str, Any]] = []
    for method in ("venmo", "cashapp"):
        for card in grouped.get(method, []):
            data = {
                "method": card.method,
                "tag": card.tag,
                "balance_cents": card.balance_cents,
                "balance_usd": cents_to_usd(card.balance_cents),
                "offset_cents": card.offset_cents,
                "received_week_cents": card.received_week_cents,
                "received_week_usd": cents_to_usd(card.received_week_cents),
                "inflow_max_cents": card.inflow_max_cents,
                "inflow_max_usd": cents_to_usd(card.inflow_max_cents),
                "inflow_maxed": bool(card.inflow_maxed),
                "sending_remaining_cents": card.sending_remaining_cents,
                "sending_remaining_usd": cents_to_usd(card.sending_remaining_cents),
                "sending_ceiling_cents": card.sending_ceiling_cents,
                "sending_ceiling_usd": cents_to_usd(card.sending_ceiling_cents),
                "send_power_maxed": bool(card.send_power_maxed),
            }
            items.append(data)
    return page(
        items,
        total=len(items),
        limit=len(items) or 1,
        offset=0,
        notes=[
            "amounts are cents; *_usd fields are derived",
            f"sending window is the rolling {_WINDOW_HOURS} hours; inflow week is"
            " America/New_York Monday 00:00 to now",
            "Round Table accounts only (method_owner=round-table)",
        ],
    )


@translate_errors
def list_blocking_sends(
    method: str, tag: str, include_pii: bool = False
) -> dict[str, Any]:
    """Outbound sends still inside the rolling 168-hour sending window for one
    Round Table account (they reduce sending_remaining_cents on its balance card),
    newest first with the time each one expires. `method` is venmo|cashapp; `tag`
    is the account handle. Amounts are cents. `recipient` masked unless include_pii."""
    method_slug = (method or "").strip().lower()
    if method_slug not in BALANCE_METHODS:
        raise bad_input("method must be venmo or cashapp")
    if not (tag or "").strip():
        raise bad_input("tag is required")
    with read_session() as session:
        rows = _list_blocking_sends(session, method_slug, tag)
    items = [
        {
            "id": row.id,
            "amount_cents": row.amount_cents,
            "amount_usd": cents_to_usd(row.amount_cents),
            "recipient": row.recipient,
            "occurred_at": row.occurred_at,
            "expires_at": row.expires_at,
        }
        for row in rows
    ]
    return page(
        items,
        total=len(items),
        limit=len(items) or 1,
        offset=0,
        include_pii=include_pii,
        notes=[
            "amounts are cents",
            f"window is {_WINDOW_HOURS} hours from occurred_at",
        ],
    )


def register(mcp: MCPServer) -> None:
    mcp.tool(name="list_outbound_sends", annotations=READ_ONLY)(list_outbound_sends)
    mcp.tool(name="get_account_balances", annotations=READ_ONLY)(get_account_balances)
    mcp.tool(name="list_blocking_sends", annotations=READ_ONLY)(list_blocking_sends)
    # write tools are added by the lead
