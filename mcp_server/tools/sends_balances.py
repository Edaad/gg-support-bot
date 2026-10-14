"""Outbound sends (money leaving Venmo/Cash App/crypto) and Round Table balances."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from mcp.server.mcpserver import Context, MCPServer

from api.account_balances import (
    METHODS as BALANCE_METHODS,
)
from api.account_balances import (
    WINDOW,
    AccountBalanceCard,
    list_account_balances,
)
from api.account_balances import (
    list_blocking_sends as _list_blocking_sends,
)
from api.account_balances import (
    normalize_tag as _balance_tag,
)
from api.account_balances import (
    update_account_balance_settings as _update_balance_settings,
)
from api.method_owner import METHOD_OWNERS, normalize_method_owner
from api.outbound_sends import (
    METHODS as SEND_METHODS,
)
from api.outbound_sends import (
    create_outbound_send,
    delete_outbound_send as _delete_outbound_send,
    normalize_tag as _send_tag,
    parse_positive_amount_cents,
    tag_matches_deposit_variant,
    update_outbound_send as _update_outbound_send,
)
from api.outbound_sends import list_outbound_sends as _list_outbound_sends
from db.models import OutboundSend
from mcp_server.auth import actor_from_context
from mcp_server.dates import parse_range
from mcp_server.db import read_session, write_session
from mcp_server.envelope import dry_run, page, write_result
from mcp_server.errors import bad_input, not_found, translate_errors
from mcp_server.serialize import cents_to_usd
from mcp_server.tools._common import (
    DESTRUCTIVE,
    READ_ONLY,
    WRITE,
    WRITE_NON_IDEMPOTENT,
    clamp_limit,
    clamp_offset,
)

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
    (@venmo, $cashapp). `from_`/`to` filter paid_at, or created_at when paid_at
    is missing (UTC datetimes; YYYY-MM-DD
    means that whole America/New_York day). Amounts are amount_cents (+ amount_usd).
    `recipient` is masked unless include_pii. See get_account_balances for totals."""
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    start, stop = parse_range(from_, to)
    if stop is not None:
        # paid_at (else created_at) uses <= to_dt; make the exclusive end inclusive.
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


def _card_dict(card: AccountBalanceCard) -> dict[str, Any]:
    return {
        "method": card.method,
        "tag": card.tag,
        "balance_cents": card.balance_cents,
        "balance_usd": cents_to_usd(card.balance_cents),
        "offset_cents": card.offset_cents,
        "offset_usd": cents_to_usd(card.offset_cents),
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
            items.append(_card_dict(card))
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
    # write tools are registered in register_writes


# ── Write tools ──────────────────────────────────────────────────────────────

_SEND_NOTES = (
    "amount is dollars; stored as amount_cents",
    "sends reduce the Round Table balance cards' running balance and, for 168 hours"
    " from paid_at (or created_at), their remaining send capacity",
)
_SEND_METHOD_DOC = "|".join(sorted(SEND_METHODS))
_OWNER_DOC = "|".join(sorted(METHOD_OWNERS))


def _send_method(method: str) -> str:
    slug = (method or "").strip().lower()
    if slug not in SEND_METHODS:
        raise bad_input(f"method must be one of {_SEND_METHOD_DOC}")
    return slug


def _send_owner(method_owner: str) -> str:
    try:
        return normalize_method_owner(method_owner)
    except ValueError as exc:
        raise bad_input(str(exc)) from exc


def _send_amount_cents(amount: float) -> int:
    try:
        return parse_positive_amount_cents(amount)
    except ValueError as exc:
        raise bad_input(str(exc)) from exc


def _load_send(session, send_id: int) -> OutboundSend:
    row = session.get(OutboundSend, int(send_id))
    if row is None:
        raise not_found(f"outbound send {send_id}")
    return row


def _send_fields(
    *,
    method: str,
    tag: str,
    method_owner: str,
    recipient: str,
    amount_cents: int,
    source_external_id: str,
    paid_at: str | None,
) -> dict[str, Any]:
    """Normalised fields as the service will store them (ValueError -> bad_input)."""
    slug = _send_method(method)
    try:
        stored_tag = _send_tag(slug, tag)
    except ValueError as exc:
        raise bad_input(str(exc)) from exc
    who = " ".join((recipient or "").split())
    if not who:
        raise bad_input("recipient is required")
    source_id = (source_external_id or "").strip()
    if not source_id:
        raise bad_input("source_external_id is required")
    return {
        "method": slug,
        "tag": stored_tag,
        "method_owner": _send_owner(method_owner),
        "recipient": who,
        "amount_cents": int(amount_cents),
        "amount_usd": cents_to_usd(amount_cents),
        "source_external_id": source_id,
        "paid_at": (paid_at or "").strip() or None,
    }


@translate_errors
def record_outbound_send(
    method: str,
    tag: str,
    amount: float,
    recipient: str,
    paid_at: str | None = None,
    source_external_id: str | None = None,
    method_owner: str = "round-table",
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Record money that left a sending account by hand, as the dashboard Sends
    page's "Add send" form does (Zapier normally ingests these). `method` is
    venmo|cashapp|crypto, `tag` the sending handle (@venmo / $cashapp / wallet
    label), `amount` is dollars (> 0, stored as cents), `recipient` who received
    it, `paid_at` an ISO-8601 datetime (recommended; omitted = created now) and
    `method_owner` round-table|vaughn|mateos (default round-table). A unique
    `source_external_id` is generated when omitted. Sends to a Round Table
    venmo/cashapp tag lower that balance card's balance and its remaining send
    capacity for the next 168 hours. No notifications. Without confirm=true
    returns a dry run of the row that would be created."""
    actor = actor_from_context(ctx)
    amount_cents = _send_amount_cents(amount)
    source_id = (source_external_id or "").strip() or f"mcp-{actor}-{uuid4().hex[:12]}"
    fields = _send_fields(
        method=method,
        tag=tag,
        method_owner=method_owner,
        recipient=recipient,
        amount_cents=amount_cents,
        source_external_id=source_id,
        paid_at=paid_at,
    )
    with read_session() as session:
        duplicate = (
            session.query(OutboundSend.id)
            .filter(OutboundSend.source_external_id == fields["source_external_id"])
            .first()
        )
        if duplicate is not None:
            raise bad_input("source_external_id already exists")
        fields["tag_matched"] = tag_matches_deposit_variant(
            session, method=fields["method"], tag=fields["tag"]
        )
    notes = list(_SEND_NOTES)
    if not fields["tag_matched"]:
        notes.append("tag does not match any deposit variant (tag_matched=false)")
    if not confirm:
        return dry_run(
            actor=actor,
            current=None,
            proposed=fields,
            message="dry run: would record this outbound send",
            notes=notes,
        )
    with write_session() as session:
        row = create_outbound_send(
            session,
            method=fields["method"],
            tag=fields["tag"],
            method_owner=fields["method_owner"],
            recipient=fields["recipient"],
            amount=Decimal(amount_cents) / 100,
            source_external_id=fields["source_external_id"],
            paid_at=fields["paid_at"],
        )
        after = _send_to_dict(row)
    return write_result(
        actor=actor,
        before=None,
        after=after,
        message=(
            f"recorded {after['method']} send {after['id']} of "
            f"${after['amount_usd']:,.2f} from {after['tag']}"
        ),
        notes=notes,
    )


@translate_errors
def update_outbound_send(
    send_id: int,
    method: str | None = None,
    tag: str | None = None,
    amount: float | None = None,
    recipient: str | None = None,
    paid_at: str | None = None,
    source_external_id: str | None = None,
    method_owner: str | None = None,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Edit an outbound send (the dashboard Sends page's edit form). Only the
    fields you pass change: `method` venmo|cashapp|crypto, `tag`, `amount` in
    dollars (> 0), `recipient`, `paid_at` (ISO-8601; pass "" to clear),
    `source_external_id` (must stay unique), `method_owner` round-table|vaughn|
    mateos. tag_matched is recomputed. Changing amount, tag or paid_at moves the
    Round Table balance cards' balance and 168-hour send capacity. Without
    confirm=true returns a dry run with the current row."""
    given = {
        "method": method,
        "tag": tag,
        "amount": amount,
        "recipient": recipient,
        "paid_at": paid_at,
        "source_external_id": source_external_id,
        "method_owner": method_owner,
    }
    if all(v is None for v in given.values()):
        raise bad_input(f"provide at least one of {', '.join(given)}")
    actor = actor_from_context(ctx)
    with read_session() as session:
        row = _load_send(session, send_id)
        before = _send_to_dict(row)
        amount_cents = (
            _send_amount_cents(amount) if amount is not None else int(row.amount_cents)
        )
        merged = _send_fields(
            method=method if method is not None else str(row.method),
            tag=tag if tag is not None else str(row.tag),
            method_owner=method_owner
            if method_owner is not None
            else str(row.method_owner),
            recipient=recipient if recipient is not None else str(row.recipient),
            amount_cents=amount_cents,
            source_external_id=source_external_id
            if source_external_id is not None
            else str(row.source_external_id),
            paid_at=paid_at if paid_at is not None else row.paid_at,
        )
        if merged["source_external_id"] != str(row.source_external_id):
            clash = (
                session.query(OutboundSend.id)
                .filter(
                    OutboundSend.source_external_id == merged["source_external_id"],
                    OutboundSend.id != int(send_id),
                )
                .first()
            )
            if clash is not None:
                raise bad_input("source_external_id already exists")
        merged["tag_matched"] = tag_matches_deposit_variant(
            session, method=merged["method"], tag=merged["tag"]
        )
    proposed = {
        k: v
        for k, v in merged.items()
        if k in ("tag_matched", "amount_usd") or v != before.get(k)
    }
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed=proposed,
            message=f"dry run: would update outbound send {send_id}",
            notes=list(_SEND_NOTES),
        )
    with write_session() as session:
        row = _update_outbound_send(
            session,
            int(send_id),
            method=merged["method"],
            tag=merged["tag"],
            method_owner=merged["method_owner"],
            recipient=merged["recipient"],
            amount=Decimal(merged["amount_cents"]) / 100,
            source_external_id=merged["source_external_id"],
            paid_at=merged["paid_at"],
        )
        after = _send_to_dict(row)
    changed = [k for k in given if given[k] is not None]
    return write_result(
        actor=actor,
        before=before,
        after=after,
        message=f"updated outbound send {send_id}: {', '.join(changed)}",
        notes=list(_SEND_NOTES),
    )


@translate_errors
def delete_outbound_send(
    send_id: int, confirm: bool = False, ctx: Context | None = None
) -> dict[str, Any]:
    """Permanently delete an outbound send (the dashboard Sends page's delete
    action). Destructive: the amount is added back to the Round Table balance
    card's running balance and, if the send was inside the 168-hour window, to
    its remaining send capacity. A Zapier re-delivery with the same
    source_external_id would recreate it. Without confirm=true returns a dry run
    showing the full row that would be deleted."""
    actor = actor_from_context(ctx)
    with read_session() as session:
        before = _send_to_dict(_load_send(session, send_id))
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed={"action": "delete", "send_id": int(send_id)},
            message=(
                f"dry run: would delete {before['method']} send {send_id} of "
                f"${before['amount_usd']:,.2f}"
            ),
            notes=list(_SEND_NOTES),
        )
    with write_session() as session:
        _delete_outbound_send(session, int(send_id))
    return write_result(
        actor=actor,
        before=before,
        after=None,
        message=f"deleted {before['method']} send {send_id}",
        notes=list(_SEND_NOTES),
    )


def _balance_cents(value: float | None, label: str) -> int | None:
    if value is None:
        return None
    try:
        cents = int((Decimal(str(value)) * 100).quantize(Decimal("1")))
    except Exception as exc:  # noqa: BLE001 - Decimal raises several types
        raise bad_input(f"{label} must be a number of dollars") from exc
    return cents


def _find_card(session, method: str, tag: str) -> AccountBalanceCard:
    for card in list_account_balances(session).get(method, []):
        if card.tag == tag:
            return card
    raise not_found(f"{method} account {tag} is not a Round Table balance card")


@translate_errors
def update_account_balance_settings(
    method: str,
    tag: str,
    offset: float | None = None,
    sending_ceiling: float | None = None,
    inflow_max: float | None = None,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Set the knobs behind one Round Table balance card (the dashboard
    Balances card's settings form). `method` is venmo|cashapp, `tag` the account
    handle. All three values are dollars (stored as cents): `offset` is added
    to received - sent to give the running balance (any sign), `sending_ceiling`
    (> 0) caps sends inside the rolling 168-hour window, `inflow_max` (> 0) is the
    weekly deposit cap behind inflow_maxed. Only the values you pass change;
    unset ones keep the current (or default) value. No notifications. Without
    confirm=true returns a dry run with the current card."""
    method_slug = (method or "").strip().lower()
    if method_slug not in BALANCE_METHODS:
        raise bad_input("method must be venmo or cashapp")
    stored_tag = _balance_tag(method_slug, tag)
    if not stored_tag or stored_tag in {"@", "$"}:
        raise bad_input("tag is required")
    if offset is None and sending_ceiling is None and inflow_max is None:
        raise bad_input("provide at least one of offset, sending_ceiling, inflow_max")
    offset_cents = _balance_cents(offset, "offset")
    ceiling_cents = _balance_cents(sending_ceiling, "sending_ceiling")
    inflow_cents = _balance_cents(inflow_max, "inflow_max")
    if ceiling_cents is not None and ceiling_cents <= 0:
        raise bad_input("sending_ceiling must be greater than zero")
    if inflow_cents is not None and inflow_cents <= 0:
        raise bad_input("inflow_max must be greater than zero")
    actor = actor_from_context(ctx)
    with read_session() as session:
        before = _card_dict(_find_card(session, method_slug, stored_tag))
    target = {
        "offset_cents": offset_cents
        if offset_cents is not None
        else before["offset_cents"],
        "sending_ceiling_cents": ceiling_cents
        if ceiling_cents is not None
        else before["sending_ceiling_cents"],
        "inflow_max_cents": inflow_cents
        if inflow_cents is not None
        else before["inflow_max_cents"],
    }
    proposed = {k: v for k, v in target.items() if v != before[k]}
    notes = ["amounts are cents in current/proposed; the parameters are dollars"]
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed=proposed,
            message=f"dry run: would update {method_slug} {stored_tag} settings",
            notes=notes,
        )
    with write_session() as session:
        card = _update_balance_settings(
            session,
            method_slug,
            stored_tag,
            offset_cents=target["offset_cents"],
            sending_ceiling_cents=target["sending_ceiling_cents"],
            inflow_max_cents=target["inflow_max_cents"],
        )
        after = _card_dict(card)
    return write_result(
        actor=actor,
        before=before,
        after=after,
        message=(
            f"updated {method_slug} {stored_tag} settings: "
            f"{', '.join(proposed) or 'no change'}"
        ),
        notes=notes,
    )


def register_writes(mcp: MCPServer) -> None:
    mcp.tool(name="record_outbound_send", annotations=WRITE_NON_IDEMPOTENT)(
        record_outbound_send
    )
    mcp.tool(name="update_outbound_send", annotations=WRITE)(update_outbound_send)
    mcp.tool(name="delete_outbound_send", annotations=DESTRUCTIVE)(delete_outbound_send)
    mcp.tool(name="update_account_balance_settings", annotations=WRITE)(
        update_account_balance_settings
    )
