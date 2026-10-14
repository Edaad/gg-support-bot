"""Admin expense ledger (dashboard Expenses page)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from sqlalchemy import func
from sqlalchemy.orm import Query, joinedload

from db.models import Club, Expense
from mcp_server.dates import parse_et_day, today_et
from mcp_server.db import read_session, write_session
from mcp_server.auth import actor_from_context
from mcp_server.envelope import dry_run, page, write_result
from mcp_server.errors import bad_input, not_found, translate_errors
from mcp_server.tools._common import (
    DESTRUCTIVE,
    READ_ONLY,
    WRITE,
    WRITE_NON_IDEMPOTENT,
    clamp_limit,
    clamp_offset,
    club_name_map,
    resolve_club,
    resolve_club_id,
)


def _apply_filters(
    query: Query,
    *,
    club_id: int | None,
    pending: bool | None,
    q: str | None,
    date_from: date | None,
    date_to: date | None,
) -> Query:
    """Same filters as the dashboard list (api/routes/expenses.py)."""
    if club_id is not None:
        query = query.filter(Expense.club_id == club_id)
    if pending is not None:
        query = query.filter(Expense.pending.is_(bool(pending)))
    if date_from is not None:
        query = query.filter(Expense.expense_date >= date_from)
    if date_to is not None:
        query = query.filter(Expense.expense_date <= date_to)
    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        query = query.join(Club, Expense.club_id == Club.id).filter(
            (Expense.expense_type.ilike(needle))
            | (Expense.description.ilike(needle))
            | (Club.name.ilike(needle))
        )
    return query


@translate_errors
def list_expenses(
    club: str | None = None,
    pending: bool | None = None,
    q: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Expense ledger rows newest first (amount, expense_type, description, club,
    expense_date, pending). `from_`/`to` are YYYY-MM-DD calendar dates applied to
    expense_date (inclusive, no timezone). `pending` filters unpaid vs settled;
    `q` matches expense_type, description or club name. Amounts are dollars; notes
    carry the total_amount of every matching row. Club: id, key (round_table|
    creator_club|clubgto) or slug (round-table|creator-club|clubgto|aces-table);
    omit for all clubs."""
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    date_from = parse_et_day(from_)
    date_to = parse_et_day(to)
    if date_from and date_to and date_from > date_to:
        raise bad_input("from must be on or before to")
    with read_session() as session:
        club_id = resolve_club_id(session, club)
        filters = {
            "club_id": club_id,
            "pending": pending,
            "q": q,
            "date_from": date_from,
            "date_to": date_to,
        }
        total, total_amount = _apply_filters(
            session.query(
                func.count(Expense.id), func.coalesce(func.sum(Expense.amount), 0)
            ).select_from(Expense),
            **filters,
        ).one()
        rows = (
            _apply_filters(
                session.query(Expense).options(joinedload(Expense.club)), **filters
            )
            .order_by(Expense.expense_date.desc(), Expense.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        names = club_name_map(session)
        items = [
            {
                "id": int(row.id),
                "amount": row.amount,
                "expense_type": row.expense_type,
                "description": row.description,
                "club_id": int(row.club_id),
                "club_name": row.club.name if row.club else names.get(int(row.club_id)),
                "expense_date": row.expense_date,
                "pending": bool(row.pending),
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]
    total_amount_text = format(
        Decimal(str(total_amount or 0)).quantize(Decimal("0.01")), "f"
    )
    return page(
        items,
        total=int(total),
        limit=limit,
        offset=offset,
        notes=[
            "amounts are dollars",
            f"total_amount={total_amount_text} across {int(total)} matching rows",
            "from/to are calendar dates on expense_date (inclusive)",
        ],
    )


def register(mcp: MCPServer) -> None:
    mcp.tool(name="list_expenses", annotations=READ_ONLY)(list_expenses)
    # write tools are added by the lead


# ── Write tools ──────────────────────────────────────────────────────────────


def _expense_dict(row: Expense, club_name: str | None) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "amount": row.amount,
        "expense_type": row.expense_type,
        "description": row.description,
        "club_id": int(row.club_id),
        "club_name": club_name,
        "expense_date": row.expense_date,
        "pending": bool(row.pending),
        "created_at": getattr(row, "created_at", None),
        "updated_at": getattr(row, "updated_at", None),
    }


def _load_expense(expense_id: int) -> dict[str, Any]:
    with read_session() as session:
        row = session.get(Expense, int(expense_id))
        if row is None:
            raise not_found(f"expense {expense_id}")
        return _expense_dict(row, club_name_map(session).get(int(row.club_id)))


@translate_errors
def create_expense(
    amount: float,
    expense_type: str,
    club: str,
    expense_date: str | None = None,
    description: str | None = None,
    pending: bool = True,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Record an expense (dollars) for a club on the Expenses ledger, as the
    dashboard form does. `expense_date` is YYYY-MM-DD (default: today in
    America/New_York); `pending` marks it as not yet settled. Without
    confirm=true returns a dry run of the row that would be created."""
    if amount == 0:
        raise bad_input("amount must not be zero")
    kind = (expense_type or "").strip()
    if not kind:
        raise bad_input("expense_type is required")
    day = parse_et_day(expense_date, default=today_et())
    actor = actor_from_context(ctx)
    with read_session() as session:
        club_row = resolve_club(session, club)
        if club_row is None:
            raise bad_input("club is required")
        club_id, club_name = int(club_row.id), str(club_row.name)
    proposed = {
        "amount": round(float(amount), 2),
        "expense_type": kind,
        "description": (description or "").strip() or None,
        "club_id": club_id,
        "club_name": club_name,
        "expense_date": day,
        "pending": bool(pending),
    }
    if not confirm:
        return dry_run(
            actor=actor,
            current=None,
            proposed=proposed,
            message="dry run: would create this expense",
            notes=["amounts are dollars"],
        )
    with write_session() as session:
        row = Expense(
            amount=Decimal(str(amount)),
            expense_type=kind,
            description=proposed["description"],
            club_id=club_id,
            expense_date=day,
            pending=bool(pending),
        )
        session.add(row)
        session.flush()
        after = _expense_dict(row, club_name)
    return write_result(
        actor=actor,
        before=None,
        after=after,
        message=f"created expense {after['id']} for {club_name}",
        notes=["amounts are dollars"],
    )


@translate_errors
def update_expense(
    expense_id: int,
    amount: float | None = None,
    expense_type: str | None = None,
    description: str | None = None,
    club: str | None = None,
    expense_date: str | None = None,
    pending: bool | None = None,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Edit an expense on the Expenses ledger, as the dashboard edit form does.
    Only the fields you pass change: `amount` (dollars, non-zero), `expense_type`,
    `description` (pass "" to clear it), `club` (id, key or slug), `expense_date`
    (YYYY-MM-DD calendar date) or `pending` (false once settled). No notification
    is sent. Without confirm=true returns a dry run with the current row."""
    if (
        amount is None
        and expense_type is None
        and description is None
        and club is None
        and expense_date is None
        and pending is None
    ):
        raise bad_input(
            "provide at least one of amount, expense_type, description, club,"
            " expense_date, pending"
        )
    changes: dict[str, Any] = {}
    if amount is not None:
        if amount == 0:
            raise bad_input("amount must not be zero")
        changes["amount"] = Decimal(str(amount))
    if expense_type is not None:
        kind = expense_type.strip()
        if not kind:
            raise bad_input("expense_type is required")
        changes["expense_type"] = kind
    if description is not None:
        changes["description"] = description.strip() or None
    if expense_date is not None:
        day = parse_et_day(expense_date)
        if day is None:
            raise bad_input(f"expected a YYYY-MM-DD date, got {expense_date!r}")
        changes["expense_date"] = day
    if pending is not None:
        changes["pending"] = bool(pending)
    actor = actor_from_context(ctx)
    before = _load_expense(expense_id)
    club_name = before["club_name"]
    if club is not None:
        with read_session() as session:
            club_row = resolve_club(session, club)
            if club_row is None:
                raise bad_input("club must not be blank")
            changes["club_id"] = int(club_row.id)
            club_name = str(club_row.name)
    proposed = dict(changes)
    if "club_id" in proposed:
        proposed["club_name"] = club_name
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed=proposed,
            message=f"dry run: would update expense {expense_id}: {', '.join(changes)}",
            notes=["amounts are dollars"],
        )
    with write_session() as session:
        row = session.get(Expense, int(expense_id))
        if row is None:
            raise not_found(f"expense {expense_id}")
        for field, value in changes.items():
            setattr(row, field, value)
        row.updated_at = datetime.now(timezone.utc)
        session.flush()
        after = _expense_dict(row, club_name)
    return write_result(
        actor=actor,
        before=before,
        after=after,
        message=f"updated expense {expense_id}: {', '.join(changes)}",
        notes=["amounts are dollars"],
    )


@translate_errors
def delete_expense(
    expense_id: int,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Permanently delete an expense from the Expenses ledger, like the dashboard
    Delete button. The row is gone from list_expenses totals and exports; no
    notification is sent. To mark an expense settled instead, use
    update_expense(pending=false). Without confirm=true returns a dry run with
    the full row that would be removed."""
    actor = actor_from_context(ctx)
    before = _load_expense(expense_id)
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed={"delete": True},
            message=f"dry run: would permanently delete expense {expense_id}",
            notes=["amounts are dollars"],
        )
    with write_session() as session:
        row = session.get(Expense, int(expense_id))
        if row is None:
            raise not_found(f"expense {expense_id}")
        session.delete(row)
        session.flush()
    return write_result(
        actor=actor,
        before=before,
        after=None,
        message=(
            f"deleted expense {expense_id} ({before['expense_type']},"
            f" {before['amount']} for {before['club_name']})"
        ),
        notes=["amounts are dollars"],
    )


def register_writes(mcp: MCPServer) -> None:
    mcp.tool(name="create_expense", annotations=WRITE_NON_IDEMPOTENT)(create_expense)
    mcp.tool(name="update_expense", annotations=WRITE)(update_expense)
    mcp.tool(name="delete_expense", annotations=DESTRUCTIVE)(delete_expense)
