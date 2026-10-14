"""Export bonus records and expenses to Google Sheets and return the link."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from mcp_server.dates import parse_et_day, today_et
from mcp_server.db import read_session
from mcp_server.envelope import single
from mcp_server.errors import bad_input, translate_errors
from mcp_server.tools._common import resolve_club

MAX_EXPORT_ROWS = 20_000

EXPORT = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)

SHARE_NOTE = (
    "The Sheet is viewable by anyone who has the link; share it only with people "
    "who should see player names and amounts."
)


def _club_label(club_row) -> str:
    return str(club_row.name) if club_row is not None else "all clubs"


def _upload(name: str, content: bytes, mime: str) -> dict[str, Any]:
    from mcp_server import google_drive

    return google_drive.upload_sheet(name, content, mime)


@translate_errors
def export_bonus_records(
    from_: str,
    to: str,
    club: str | None = None,
    bonus_type_id: int | None = None,
    other: bool = False,
) -> dict[str, Any]:
    """Export bonus records issued between two America/New_York days
    (YYYY-MM-DD, inclusive) to a new Google Sheet and return its link, with the
    same columns as the dashboard's bonus CSV (player, group title, amount in
    dollars, bonus type, who issued it). Filter by `club`, `bonus_type_id`, or
    `other=true` for bonuses with no type. Only call this when the operator asks
    to export or share bonuses: it creates a file in the configured Drive folder
    that anyone with the link can view, and the call is logged."""
    from api.record_csv_export import (
        bonus_records_csv_from_rows,
        bonus_records_csv_rows,
        bonus_records_total,
    )

    start = parse_et_day(from_)
    end = parse_et_day(to)
    if start is None or end is None:
        raise bad_input("from_ and to are required YYYY-MM-DD days")
    if start > end:
        raise bad_input("from_ must be on or before to")
    if other and bonus_type_id is not None:
        raise bad_input("use either bonus_type_id or other=true, not both")
    with read_session() as session:
        club_row = resolve_club(session, club)
        data_rows = bonus_records_csv_rows(
            session,
            from_day=start,
            to_day=end,
            club_id=int(club_row.id) if club_row is not None else None,
            bonus_type_id=bonus_type_id,
            other=other,
        )
        label = _club_label(club_row)
    rows = len(data_rows)
    if rows > MAX_EXPORT_ROWS:
        raise bad_input(
            f"{rows} rows exceeds the {MAX_EXPORT_ROWS}-row export limit; narrow "
            "the date range or filter by club"
        )
    content = bonus_records_csv_from_rows(data_rows)
    total = float(round(bonus_records_total(data_rows), 2))
    name = f"GG bonuses - {label} - {start.isoformat()} to {end.isoformat()}"
    from mcp_server.google_drive import CSV_MIME

    uploaded = _upload(name, content, CSV_MIME)
    notes = [SHARE_NOTE, "amounts are dollars", "dates are America/New_York days"]
    if rows == 0:
        notes.append("no bonus records matched; the Sheet has only the header row")
    return single(
        {
            **uploaded,
            "rows": rows,
            "total_amount": total,
            "filters": {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "club": label,
                "bonus_type_id": bonus_type_id,
                "other": other,
            },
        },
        include_pii=True,
        notes=notes,
    )


@translate_errors
def export_expenses(
    club: str | None = None,
    pending: bool | None = None,
    q: str | None = None,
    from_: str | None = None,
    to: str | None = None,
) -> dict[str, Any]:
    """Export expenses to a new Google Sheet and return its link, with the same
    columns as the dashboard's Expenses export (amount in dollars, type,
    description, club, date, pending). `from_` / `to` are YYYY-MM-DD expense
    dates (inclusive; omit for all time), `pending` true/false filters by
    settlement, `q` searches type, description and club name. Only call this
    when the operator asks to export or share expenses: it creates a file in the
    configured Drive folder that anyone with the link can view, and the call is
    logged."""
    from api.routes.expenses import _filtered_query, build_expenses_xlsx

    start = parse_et_day(from_)
    end = parse_et_day(to)
    if start and end and start > end:
        raise bad_input("from_ must be on or before to")
    with read_session() as session:
        club_row = resolve_club(session, club)
        query = _filtered_query(
            session,
            club_id=int(club_row.id) if club_row is not None else None,
            pending=pending,
            q=q,
            date_from=start,
            date_to=end,
        )
        rows = query.limit(MAX_EXPORT_ROWS + 1).all()
        if len(rows) > MAX_EXPORT_ROWS:
            raise bad_input(
                f"more than {MAX_EXPORT_ROWS} expenses match; narrow the dates or "
                "filter by club"
            )
        content = build_expenses_xlsx(rows)
        total = sum((Decimal(str(r.amount or 0)) for r in rows), Decimal("0"))
        label = _club_label(club_row)
    span = (
        f"{start.isoformat() if start else 'start'} to "
        f"{end.isoformat() if end else today_et().isoformat()}"
    )
    status = "" if pending is None else (" - pending" if pending else " - settled")
    name = f"GG expenses - {label}{status} - {span}"
    from mcp_server.google_drive import XLSX_MIME

    uploaded = _upload(name, content, XLSX_MIME)
    notes = [SHARE_NOTE, "amounts are dollars"]
    if not rows:
        notes.append("no expenses matched; the Sheet has only the header row")
    return single(
        {
            **uploaded,
            "rows": len(rows),
            "total_amount": float(round(total, 2)),
            "filters": {
                "club": label,
                "pending": pending,
                "q": q,
                "from": start.isoformat() if start else None,
                "to": end.isoformat() if end else None,
            },
        },
        include_pii=True,
        notes=notes,
    )


def register(mcp: MCPServer) -> None:
    mcp.tool(name="export_bonus_records", annotations=EXPORT)(export_bonus_records)
    mcp.tool(name="export_expenses", annotations=EXPORT)(export_expenses)
