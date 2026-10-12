"""Staff cashout records (what players are owed) and the money-sent ledger."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from bot.services.staff_cashout_records import (
    STATUSES,
    get_staff_cashout_record,
    list_staff_cashout_money_sends,
    list_staff_cashout_records,
)
from mcp_server.dates import parse_range
from mcp_server.db import read_session
from mcp_server.auth import actor_from_context
from mcp_server.envelope import dry_run, page, single, write_result
from mcp_server.errors import bad_input, not_found, translate_errors
from mcp_server.tools._common import (
    READ_ONLY,
    WRITE,
    clamp_limit,
    clamp_offset,
    club_name_map,
    resolve_club_id,
)

_RECORD_NOTES = (
    "amounts are dollars",
    "payout_details masked unless include_pii",
    "status is derived from the ledger: active (sent < amount), cleared, oversent;"
    " do_not_send records are excluded unless status=do_not_send",
)


def _compact_record(data: dict[str, Any]) -> dict[str, Any]:
    """Drop proof bytes defensively; the service already returns metadata only."""
    out = dict(data)
    out["sends"] = [
        {k: v for k, v in send.items() if k != "content"}
        for send in out.get("sends", [])
    ]
    return out


@translate_errors
def list_cashout_records(
    club: str | None = None,
    status: str | None = None,
    q: str | None = None,
    audited: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    include_pii: bool = False,
) -> dict[str, Any]:
    """Staff cashout records newest first, each with its payout destinations
    (payments) and money-sent rows (sends) plus sent/remaining/status ledger.
    `status` is active|cleared|oversent|do_not_send; `q` matches group title,
    gg_player_id, club name or payout details; `audited` filters the audit flag.
    Amounts are dollars. Club: id, key (round_table|creator_club|clubgto) or
    slug (round-table|creator-club|clubgto|aces-table); omit for all clubs."""
    if status is not None and status not in STATUSES:
        raise bad_input("status must be active, cleared, oversent, or do_not_send")
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    with read_session() as session:
        club_id = resolve_club_id(session, club)
    rows, total = list_staff_cashout_records(
        club_id=club_id,
        status=status,
        q=q,
        audited=audited,
        limit=limit,
        offset=offset,
    )
    items = [_compact_record(row) for row in rows]
    return page(
        items,
        total=total,
        limit=limit,
        offset=offset,
        include_pii=include_pii,
        notes=_RECORD_NOTES,
    )


@translate_errors
def get_cashout_record(record_id: int, include_pii: bool = False) -> dict[str, Any]:
    """One staff cashout record by id with its payout destinations, money-sent
    rows, ledger (sent/remaining/status), audit flag and note. Amounts are dollars;
    payout_details and sender names are masked unless include_pii."""
    data = get_staff_cashout_record(int(record_id))
    if data is None:
        raise not_found(f"cashout record {record_id}")
    with read_session() as session:
        names = club_name_map(session)
    out = _compact_record(data)
    club_id = out.get("club_id")
    out["club_name"] = names.get(int(club_id)) if club_id is not None else None
    return single(out, include_pii=include_pii, notes=_RECORD_NOTES[:2])


@translate_errors
def list_cashout_money_sends(
    club: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    method: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    include_pii: bool = False,
) -> dict[str, Any]:
    """Flat ledger of money actually sent to players across cashouts, newest first
    (who sent, how much, by which method, for which record/player). `from_`/`to`
    filter created_at (UTC datetimes; YYYY-MM-DD means that whole America/New_York
    day, inclusive). `method` is an exact method_display_name; `q` matches sender,
    group title or gg_player_id. Amounts are dollars. Club: id, key
    (round_table|creator_club|clubgto) or slug (round-table|creator-club|clubgto|
    aces-table); omit for all clubs."""
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    start, stop = parse_range(from_, to, naive=True)
    if stop is not None:
        # The service filters created_at <= to_dt; make the exclusive end inclusive.
        stop = stop - timedelta(microseconds=1)
    with read_session() as session:
        club_id = resolve_club_id(session, club)
    rows, total = list_staff_cashout_money_sends(
        club_id=club_id,
        from_dt=start,
        to_dt=stop,
        method_display_name=method,
        q=q,
        limit=limit,
        offset=offset,
    )
    return page(
        rows,
        total=total,
        limit=limit,
        offset=offset,
        include_pii=include_pii,
        notes=[
            "amounts are dollars",
            "sender_name masked unless include_pii",
            "see get_cashout_record for the parent record",
        ],
    )


def register(mcp: MCPServer) -> None:
    mcp.tool(name="list_cashout_records", annotations=READ_ONLY)(list_cashout_records)
    mcp.tool(name="get_cashout_record", annotations=READ_ONLY)(get_cashout_record)
    mcp.tool(name="list_cashout_money_sends", annotations=READ_ONLY)(
        list_cashout_money_sends
    )
    # write tools are added by the lead


# ── Write tools ──────────────────────────────────────────────────────────────


@translate_errors
def update_cashout_record(
    record_id: int,
    note: str | None = None,
    audited: bool | None = None,
    sending: bool | None = None,
    do_not_send: bool | None = None,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Annotate a staff cashout record like the dashboard: set a staff `note`,
    the `audited` flag, `sending` (payout in progress) or `do_not_send` (hold).
    Only the fields you pass change; amount and group title are not editable
    here. Without confirm=true returns a dry run with the current record."""
    from bot.services.staff_cashout_records import (
        CashoutRecordNotActive,
        get_staff_cashout_record,
        update_staff_cashout_record,
    )

    if note is None and audited is None and sending is None and do_not_send is None:
        raise bad_input("provide at least one of note, audited, sending, do_not_send")
    actor = actor_from_context(ctx)
    before = get_staff_cashout_record(int(record_id))
    if before is None:
        raise not_found(f"cashout record {record_id}")
    proposed = {
        k: v
        for k, v in {
            "note": note,
            "audited": audited,
            "sending": sending,
            "do_not_send": do_not_send,
        }.items()
        if v is not None
    }
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed=proposed,
            message="dry run: would update the cashout record",
        )
    try:
        after = update_staff_cashout_record(
            int(record_id),
            sending=sending,
            do_not_send=do_not_send,
            audited=audited,
            note=note,
            note_set=note is not None,
        )
    except CashoutRecordNotActive as exc:
        raise ToolError(f"conflict: {exc}") from exc
    if after is None:
        raise not_found(f"cashout record {record_id}")
    return write_result(
        actor=actor,
        before=before,
        after=after,
        message=f"updated cashout record {record_id}: {', '.join(proposed)}",
    )


def register_writes(mcp: MCPServer) -> None:
    mcp.tool(name="update_cashout_record", annotations=WRITE)(update_cashout_record)
