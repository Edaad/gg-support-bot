"""Pool Pay tools: manual (trade-request) deposit requests and shared union methods.

Read tools only. Write tools (update_manual_deposit_request) are added by the lead.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import joinedload

from api.routes.manual_deposit_requests import (
    _apply_list_order,
    _list_query,
    _list_summary,
    _to_read as manual_deposit_to_read,
)
from api.routes.union_methods import (
    _row_clubs_for_methods,
    _stats_for_methods,
    _to_read as union_method_to_read,
)
from bot.services.deposit_union_types import validate_deposit_union
from bot.services.pool_pay_types import validate_pool_pay_type
from bot.services.union_method_types import validate_union_method_type
from db.models import ClubPaymentMethod, ClubPaymentMethodClub
from mcp_server.dates import parse_range
from mcp_server.db import read_session
from mcp_server.auth import actor_from_context
from mcp_server.envelope import dry_run, page, write_result
from mcp_server.errors import bad_input, not_found, translate_errors
from mcp_server.tools._common import (
    READ_ONLY,
    WRITE,
    clamp_limit,
    clamp_offset,
    club_name_map,
    resolve_club_id,
)


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


@translate_errors
def list_manual_deposit_requests(
    club: str | None = None,
    method_id: int | None = None,
    method_slug: str | None = None,
    method_type: str | None = None,
    deposit_union: str | None = None,
    pool_pay_type: str | None = None,
    trade_record_checked: bool | None = None,
    variant: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    q: str | None = None,
    include_inactive_methods: bool = True,
    limit: int = 50,
    offset: int = 0,
    include_pii: bool = False,
) -> dict[str, Any]:
    """Pool Pay manual deposit requests (player said they paid a shared union
    method; staff tick trade_record_checked once the trade record confirms it),
    newest first. Filters: method_id / method_slug (pool-pay slug), method_type
    zelle|cashapp|applepay|venmo, deposit_union tmt|massiv, pool_pay_type
    union_method|large_cashout, trade_record_checked true/false (omit for both),
    variant name, `q` on group title / club / slug / amount. from/to are UTC
    datetimes on created_at. Amounts are dollars. Club: Club id (int), key (round_table|creator_club|clubgto) or slug (round-table|creator-club|clubgto|aces-table). Omit for all clubs."""
    method_type = _clean(method_type)
    deposit_union = _clean(deposit_union)
    pool_pay_type = _clean(pool_pay_type)
    if method_type:
        method_type = validate_union_method_type(method_type)
    if deposit_union:
        deposit_union = validate_deposit_union(deposit_union)
    if pool_pay_type:
        pool_pay_type = validate_pool_pay_type(pool_pay_type)
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    from_dt, to_dt = parse_range(from_, to)

    with read_session() as session:
        club_id = resolve_club_id(session, club)
        query = _list_query(
            session,
            club_id=club_id,
            method_id=int(method_id) if method_id is not None else None,
            method_slug=_clean(method_slug),
            method_type=method_type,
            deposit_union=deposit_union,
            pool_pay_type=pool_pay_type,
            trade_record_checked=trade_record_checked,
            include_inactive_methods=include_inactive_methods,
            variant=_clean(variant),
            from_dt=from_dt,
            to_dt=to_dt,
            q=_clean(q),
        )
        summary = _list_summary(query)
        rows = _apply_list_order(query).offset(offset).limit(limit).all()
        names = club_name_map(session)
        items = []
        for row in rows:
            data = manual_deposit_to_read(row).model_dump()
            data["club_name"] = names.get(int(row.club_id))
            items.append(data)
    notes = [
        "amounts are dollars",
        f"summary: {summary.total_count} requests, "
        f"${float(summary.total_amount or 0):,.2f}",
    ]
    if not include_inactive_methods:
        notes.append("requests on retired methods excluded")
    return page(
        items,
        total=int(summary.total_count),
        limit=limit,
        offset=offset,
        include_pii=include_pii,
        notes=notes,
    )


@translate_errors
def list_union_methods(
    club: str | None = None,
    is_active: bool | None = None,
    deposit_union: str | None = None,
    pool_pay_type: str | None = None,
    include_pii: bool = False,
) -> dict[str, Any]:
    """Shared Pool Pay methods (tracks_manual_requests): type zelle|cashapp|applepay|
    venmo, deposit_union tmt|massiv, pool_pay_type union_method|large_cashout,
    deposit_limit vs used_sum, unchecked_count / deposit_request_count, the member
    clubs that may offer it and the clubs that actually used it (row_clubs).
    `club` keeps methods owned by or shared with that club. Amounts are dollars.
    The receiving tag and account name are masked unless include_pii. Club: Club id (int), key (round_table|creator_club|clubgto) or slug (round-table|creator-club|clubgto|aces-table). Omit for all clubs."""
    deposit_union = _clean(deposit_union)
    pool_pay_type = _clean(pool_pay_type)
    if deposit_union:
        deposit_union = validate_deposit_union(deposit_union)
    if pool_pay_type:
        pool_pay_type = validate_pool_pay_type(pool_pay_type)

    with read_session() as session:
        club_id = resolve_club_id(session, club)
        query = (
            session.query(ClubPaymentMethod)
            .options(
                joinedload(ClubPaymentMethod.method_clubs).joinedload(
                    ClubPaymentMethodClub.club
                )
            )
            .filter(ClubPaymentMethod.tracks_manual_requests.is_(True))
        )
        if is_active is not None:
            query = query.filter(ClubPaymentMethod.is_active.is_(bool(is_active)))
        if deposit_union:
            query = query.filter(ClubPaymentMethod.deposit_union == deposit_union)
        if pool_pay_type:
            query = query.filter(ClubPaymentMethod.pool_pay_type == pool_pay_type)
        if club_id is not None:
            shared = exists(
                select(1).where(
                    ClubPaymentMethodClub.method_id == ClubPaymentMethod.id,
                    ClubPaymentMethodClub.club_id == club_id,
                )
            )
            query = query.filter(or_(ClubPaymentMethod.club_id == club_id, shared))
        methods = query.order_by(
            ClubPaymentMethod.union_type,
            ClubPaymentMethod.sort_order,
            ClubPaymentMethod.id,
        ).all()
        method_ids = [int(m.id) for m in methods]
        stats = _stats_for_methods(session, method_ids)
        row_clubs = _row_clubs_for_methods(session, method_ids)
        names = club_name_map(session)
        items = []
        for method in methods:
            used_sum, unchecked, count = stats.get(int(method.id), (Decimal("0"), 0, 0))
            data = union_method_to_read(
                method, used_sum, unchecked, count, row_clubs.get(int(method.id), [])
            ).model_dump()
            # ``tag`` is a masked PII key; ``method_tag`` is not.
            data["tag"] = data.pop("method_tag", None)
            data["owner_club_id"] = int(method.club_id)
            data["owner_club_name"] = names.get(int(method.club_id))
            data["direction"] = method.direction
            data["remaining_limit"] = (
                Decimal(str(method.deposit_limit)) - Decimal(str(used_sum))
                if method.deposit_limit is not None
                else None
            )
            items.append(data)
    return page(
        items,
        total=len(items),
        limit=len(items) or 1,
        offset=0,
        include_pii=include_pii,
        notes=["amounts are dollars", "no offset paging"],
    )


def register(mcp: MCPServer) -> None:
    mcp.tool(name="list_manual_deposit_requests", annotations=READ_ONLY)(
        list_manual_deposit_requests
    )
    mcp.tool(name="list_union_methods", annotations=READ_ONLY)(list_union_methods)
    # write tools are added by the lead (update_manual_deposit_request)


# ── Write tools ──────────────────────────────────────────────────────────────


def _request_snapshot(request_id: int) -> dict[str, Any]:
    from db.models import ManualDepositRequest

    with read_session() as session:
        row = (
            session.query(ManualDepositRequest)
            .options(joinedload(ManualDepositRequest.club))
            .filter(ManualDepositRequest.id == int(request_id))
            .first()
        )
        if row is None:
            raise not_found(f"manual deposit request {request_id}")
        return manual_deposit_to_read(row).model_dump()


@translate_errors
def update_manual_deposit_request(
    request_id: int,
    trade_record_checked: bool | None = None,
    amount: float | None = None,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Edit a Pool Pay manual deposit request: mark it trade-record checked (or
    unchecked) and/or correct its dollar amount, as the dashboard PATCH does.
    At least one of trade_record_checked / amount is required. Without
    confirm=true returns a dry run with the current row and the proposed change."""
    from bot.services.manual_deposit_requests import (
        update_dashboard_manual_deposit_request,
    )

    if trade_record_checked is None and amount is None:
        raise bad_input("provide trade_record_checked and/or amount")
    if amount is not None and amount <= 0:
        raise bad_input("amount must be greater than zero")
    actor = actor_from_context(ctx)
    before = _request_snapshot(request_id)
    proposed: dict[str, Any] = {}
    if trade_record_checked is not None:
        proposed["trade_record_checked"] = bool(trade_record_checked)
    if amount is not None:
        proposed["amount"] = round(float(amount), 2)
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed=proposed,
            message="dry run: would update the manual deposit request",
            notes=["amounts are dollars"],
        )
    update_dashboard_manual_deposit_request(
        request_id=int(request_id),
        amount=Decimal(str(amount)) if amount is not None else None,
        trade_record_checked=trade_record_checked,
    )
    after = _request_snapshot(request_id)
    return write_result(
        actor=actor,
        before=before,
        after=after,
        message=f"updated manual deposit request {request_id}",
        notes=["amounts are dollars"],
    )


def register_writes(mcp: MCPServer) -> None:
    mcp.tool(name="update_manual_deposit_request", annotations=WRITE)(
        update_manual_deposit_request
    )
