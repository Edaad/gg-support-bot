"""Staff cashout records (what players are owed) and the money-sent ledger."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation
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

_DOLLARS = "amounts are dollars"
_CREATE_NOTIFY_NOTE = (
    "creating fires the 'New cashout' Pushover alert to the configured recipients;"
    " while the record stays active the bot worker's Slack reminders include it"
)
_DESTINATION_RULES = (
    "catalog methods (payment_method_id) require payout_details and, when the"
    " method has sub-options, payment_sub_option_id; custom methods require"
    " method_display_name; a bare Venmo @handle is stored as its payment link"
)
_SEND_LEDGER_NOTE = (
    "sends feed the money-sent ledger, balance cards and weekly audits;"
    " remaining/status are recomputed from them"
)
_OWED_PIN_NOTE = (
    "when a send covers the full amount the bot worker clears the '$X owed' pin in"
    " the player's group (records linked to a chat only)"
)
_PLAYER_NOTIFY_NOTE = (
    "notify=true queues a 'Sent $X!' message to the player's group, posted by the"
    " club account from the bot worker; it only applies when chat_connected is true"
)
_DESTINATION_FIELDS = (
    "payment_method_id",
    "payment_sub_option_id",
    "method_display_name",
    "payout_details",
    "amount",
)


def _to_decimal(value: Any, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise bad_input(f"{label} must be a number") from exc
    if not number.is_finite():
        raise bad_input(f"{label} must be a number")
    return number


def _positive_amount(value: Any, label: str = "amount") -> Decimal:
    number = _to_decimal(value, label)
    if number <= 0:
        raise bad_input(f"{label} must be greater than zero")
    return number


def _load_record(record_id: int) -> dict[str, Any]:
    data = get_staff_cashout_record(int(record_id))
    if data is None:
        raise not_found(f"cashout record {record_id}")
    with read_session() as session:
        names = club_name_map(session)
    out = _compact_record(data)
    club_id = out.get("club_id")
    out["club_name"] = names.get(int(club_id)) if club_id is not None else None
    return out


def _record_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record.get(key)
        for key in (
            "id",
            "club_id",
            "club_name",
            "group_title",
            "gg_player_id",
            "amount",
            "sent",
            "remaining",
            "status",
        )
    }


def _find_child(record: dict[str, Any], key: str, child_id: int) -> dict[str, Any]:
    for row in record.get(key, []):
        if int(row.get("id")) == int(child_id):
            return row
    label = "destination" if key == "payments" else "send"
    raise not_found(f"{label} {child_id} on cashout record {record['id']}")


def _destination_input(
    *,
    payment_method_id: int | None,
    payment_sub_option_id: int | None,
    method_display_name: str | None,
    payout_details: str | None,
    amount: float | None,
) -> dict[str, Any]:
    """Only the fields that were passed, with the per-destination amount as Decimal."""
    out: dict[str, Any] = {}
    if payment_method_id is not None:
        out["payment_method_id"] = int(payment_method_id)
    if payment_sub_option_id is not None:
        out["payment_sub_option_id"] = int(payment_sub_option_id)
    if method_display_name is not None:
        out["method_display_name"] = str(method_display_name)
    if payout_details is not None:
        out["payout_details"] = str(payout_details)
    if amount is not None:
        out["amount"] = _positive_amount(amount, "destination amount")
    return out


def _validate_destination(pdata: dict[str, Any]) -> dict[str, Any]:
    """Normalise a destination the way the service will store it (dry-run echo)."""
    from bot.services.staff_cashout_records import _validate_method_choice

    method_id, sub_id, display, details = _validate_method_choice(
        payment_method_id=pdata.get("payment_method_id"),
        payment_sub_option_id=pdata.get("payment_sub_option_id"),
        method_display_name=pdata.get("method_display_name"),
        payout_details=pdata.get("payout_details"),
        require_payout_details=pdata.get("payment_method_id") is not None,
    )
    return {
        "payment_method_id": method_id,
        "payment_sub_option_id": sub_id,
        "method_display_name": display,
        "payout_details": details,
        "amount": pdata.get("amount"),
    }


def _parse_payments(payments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not isinstance(payments, list) or not payments:
        raise bad_input("payments must be a non-empty list of destinations")
    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(payments):
        if not isinstance(item, dict):
            raise bad_input(f"payments[{idx}] must be an object")
        unknown = sorted(set(item) - set(_DESTINATION_FIELDS))
        if unknown:
            raise bad_input(
                f"payments[{idx}] has unknown keys {unknown}; allowed:"
                f" {', '.join(_DESTINATION_FIELDS)}"
            )
        rows.append(
            _destination_input(
                payment_method_id=item.get("payment_method_id"),
                payment_sub_option_id=item.get("payment_sub_option_id"),
                method_display_name=item.get("method_display_name"),
                payout_details=item.get("payout_details"),
                amount=item.get("amount"),
            )
        )
    return rows


@translate_errors
def create_cashout_record(
    club: str,
    group_title: str,
    amount: float,
    payments: list[dict[str, Any]],
    note: str | None = None,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Create a staff cashout record (what a player is owed) as the dashboard
    "New cashout" form does. `club` is an id, key (round_table|creator_club|
    clubgto) or slug; `group_title` is the player's group title ("GTO / 8190-5287
    / name", the gg_player_id is parsed from it); `amount` is dollars and is
    recorded as given, cents included. `payments` is a non-empty list of payout
    destinations, each {payment_method_id?, payment_sub_option_id?,
    method_display_name?, payout_details?, amount?}: a catalog method (payment_method_id from
    list_payment_methods, direction=cashout) needs payout_details; a custom method
    needs method_display_name. Like the dashboard, the record is not linked to a
    Telegram chat (no owed pin / player notify). Creating fires the "New cashout"
    Pushover alert and the record joins the Slack reminder cycle while active.
    Without confirm=true returns a dry run with the validated destinations."""
    from bot.services.staff_cashout_records import (
        clean_cashout_note,
        create_staff_cashout_record_manual,
    )
    from bot.services.player_details import parse_tracking_title

    title = (group_title or "").strip()
    if not title:
        raise bad_input("group_title is required")
    amount_value = _positive_amount(amount)
    rows = _parse_payments(payments)
    clean_note = clean_cashout_note(note)
    actor = actor_from_context(ctx)
    with read_session() as session:
        club_row = resolve_club(session, club)
        if club_row is None:
            raise bad_input("club is required")
        club_id, club_name = int(club_row.id), str(club_row.name)
    validated = [_validate_destination(row) for row in rows]
    parsed = parse_tracking_title(title)
    proposed = {
        "club_id": club_id,
        "club_name": club_name,
        "group_title": title,
        "gg_player_id": parsed[1] if parsed else None,
        "amount": amount_value,
        "note": clean_note,
        "trigger": "dashboard",
        "tracks_money_sent": True,
        "payments": validated,
    }
    notes = [_DOLLARS, _DESTINATION_RULES, _CREATE_NOTIFY_NOTE]
    if not confirm:
        return dry_run(
            actor=actor,
            current=None,
            proposed=proposed,
            message=f"dry run: would create a {amount_value} cashout record for {title}",
            notes=notes,
        )
    data = create_staff_cashout_record_manual(
        club_id=club_id,
        group_title=title,
        amount=amount_value,
        payments=rows,
        note=clean_note,
    )
    after = _compact_record(data)
    after["club_name"] = club_name
    return write_result(
        actor=actor,
        before=None,
        after=after,
        message=(
            f"created cashout record {after['id']} for {title}: {amount_value}"
            f" via {len(validated)} destination(s)"
        ),
        notes=notes,
    )


@translate_errors
def delete_cashout_record(
    record_id: int,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Permanently delete a staff cashout record together with its payout
    destinations and money-sent rows, like the dashboard Delete button. Works on
    any status and no notification is sent. Prefer
    update_cashout_record(do_not_send=true) to hold a payout instead. Without
    confirm=true returns a dry run with the full record that would be removed."""
    from bot.services.staff_cashout_records import delete_staff_cashout_record

    actor = actor_from_context(ctx)
    before = _load_record(record_id)
    notes = [
        _DOLLARS,
        f"deletes {len(before.get('payments', []))} destination(s) and"
        f" {len(before.get('sends', []))} money-sent row(s) with the record",
    ]
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed={"delete": True},
            message=f"dry run: would permanently delete cashout record {record_id}",
            notes=notes,
        )
    if not delete_staff_cashout_record(int(record_id)):
        raise not_found(f"cashout record {record_id}")
    return write_result(
        actor=actor,
        before=before,
        after=None,
        message=f"deleted cashout record {record_id} ({before.get('group_title')})",
        notes=notes,
    )


@translate_errors
def update_cashout_record(
    record_id: int,
    note: str | None = None,
    audited: bool | None = None,
    sending: bool | None = None,
    do_not_send: bool | None = None,
    amount: float | None = None,
    group_title: str | None = None,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Edit a staff cashout record like the dashboard: set a staff `note`, the
    `audited` flag (only once remaining is zero), `sending` (payout in progress),
    `do_not_send` (hold), the original `amount` (dollars; only while the record is
    active) or the `group_title` (the gg_player_id is re-parsed from it). Only the
    fields you pass change. Destinations and sends have their own tools
    (add_cashout_destination, add_cashout_send, ...). Without confirm=true returns
    a dry run with the current record."""
    from bot.services.staff_cashout_records import (
        CashoutRecordNotActive,
        update_staff_cashout_record,
    )

    if (
        note is None
        and audited is None
        and sending is None
        and do_not_send is None
        and amount is None
        and group_title is None
    ):
        raise bad_input(
            "provide at least one of note, audited, sending, do_not_send, amount,"
            " group_title"
        )
    new_amount = _positive_amount(amount) if amount is not None else None
    title = group_title.strip() if group_title is not None else None
    if title is not None and not title:
        raise bad_input("group_title must not be blank")
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
            "amount": new_amount,
            "group_title": title,
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
            group_title=title,
            amount=new_amount,
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


@translate_errors
def add_cashout_destination(
    record_id: int,
    method_display_name: str | None = None,
    payment_method_id: int | None = None,
    payment_sub_option_id: int | None = None,
    payout_details: str | None = None,
    amount: float | None = None,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Add a payout destination (where to send the player's money) to a staff
    cashout record, like "Add destination" on the dashboard record page. Either
    pass a catalog `payment_method_id` (list_payment_methods, direction=cashout;
    plus `payment_sub_option_id` when the method has sub-options) with
    `payout_details` (handle/email/address), or a custom `method_display_name`
    with optional `payout_details`. `amount` (dollars) optionally splits the
    cashout across destinations. No notification is sent. Without confirm=true
    returns a dry run with the validated destination."""
    from bot.services.staff_cashout_records import add_staff_cashout_payment

    pdata = _destination_input(
        payment_method_id=payment_method_id,
        payment_sub_option_id=payment_sub_option_id,
        method_display_name=method_display_name,
        payout_details=payout_details,
        amount=amount,
    )
    if "payment_method_id" not in pdata and "method_display_name" not in pdata:
        raise bad_input("pass payment_method_id or method_display_name")
    actor = actor_from_context(ctx)
    before = _load_record(record_id)
    proposed = _validate_destination(pdata)
    notes = [_DOLLARS, _DESTINATION_RULES]
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed=proposed,
            message=(
                f"dry run: would add a {proposed['method_display_name']} destination"
                f" to cashout record {record_id}"
            ),
            notes=notes,
        )
    after = add_staff_cashout_payment(int(record_id), pdata)
    if after is None:
        raise not_found(f"cashout record {record_id}")
    return write_result(
        actor=actor,
        before=before,
        after=_compact_record(after),
        message=(
            f"added {proposed['method_display_name']} destination to cashout record"
            f" {record_id}"
        ),
        notes=notes,
    )


@translate_errors
def update_cashout_destination(
    record_id: int,
    payment_id: int,
    method_display_name: str | None = None,
    payment_method_id: int | None = None,
    payment_sub_option_id: int | None = None,
    payout_details: str | None = None,
    amount: float | None = None,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Edit one payout destination of a staff cashout record (dashboard "Edit
    destination"). `payment_id` is the destination id from get_cashout_record.
    Only the fields you pass change and the merged destination is re-validated:
    a catalog method still needs payout_details, a custom one a display name.
    Fields cannot be cleared here; use remove_cashout_destination +
    add_cashout_destination for that. `amount` is dollars. Without confirm=true
    returns a dry run with the destination as it would be stored."""
    from bot.services.staff_cashout_records import update_staff_cashout_payment

    pdata = _destination_input(
        payment_method_id=payment_method_id,
        payment_sub_option_id=payment_sub_option_id,
        method_display_name=method_display_name,
        payout_details=payout_details,
        amount=amount,
    )
    if not pdata:
        raise bad_input(
            "provide at least one of method_display_name, payment_method_id,"
            " payment_sub_option_id, payout_details, amount"
        )
    actor = actor_from_context(ctx)
    before = _load_record(record_id)
    existing = _find_child(before, "payments", payment_id)
    merged = {key: existing.get(key) for key in _DESTINATION_FIELDS}
    merged.update(pdata)
    proposed = _validate_destination(merged)
    notes = [_DOLLARS, _DESTINATION_RULES]
    if not confirm:
        return dry_run(
            actor=actor,
            current={"record": _record_summary(before), "destination": existing},
            proposed=proposed,
            message=(
                f"dry run: would update destination {payment_id} on cashout record"
                f" {record_id}: {', '.join(pdata)}"
            ),
            notes=notes,
        )
    after = update_staff_cashout_payment(int(record_id), int(payment_id), pdata)
    if after is None:
        raise not_found(f"destination {payment_id} on cashout record {record_id}")
    return write_result(
        actor=actor,
        before=before,
        after=_compact_record(after),
        message=(
            f"updated destination {payment_id} on cashout record {record_id}:"
            f" {', '.join(pdata)}"
        ),
        notes=notes,
    )


@translate_errors
def remove_cashout_destination(
    record_id: int,
    payment_id: int,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Remove one payout destination from a staff cashout record (dashboard
    "Remove destination"). `payment_id` is the destination id from
    get_cashout_record. The record, its other destinations and its money-sent
    rows are untouched; no notification is sent. Without confirm=true returns a
    dry run with the full destination row that would be removed."""
    from bot.services.staff_cashout_records import delete_staff_cashout_payment

    actor = actor_from_context(ctx)
    before = _load_record(record_id)
    existing = _find_child(before, "payments", payment_id)
    if not confirm:
        return dry_run(
            actor=actor,
            current={"record": _record_summary(before), "destination": existing},
            proposed={"delete": True},
            message=(
                f"dry run: would remove {existing.get('method_display_name')}"
                f" destination {payment_id} from cashout record {record_id}"
            ),
            notes=[_DOLLARS],
        )
    after = delete_staff_cashout_payment(int(record_id), int(payment_id))
    if after is None:
        raise not_found(f"destination {payment_id} on cashout record {record_id}")
    return write_result(
        actor=actor,
        before=before,
        after=_compact_record(after),
        message=(
            f"removed {existing.get('method_display_name')} destination {payment_id}"
            f" from cashout record {record_id}"
        ),
        notes=[_DOLLARS],
    )


@translate_errors
def add_cashout_send(
    record_id: int,
    amount: float,
    method_display_name: str,
    sender_name: str | None = None,
    proof_link: str | None = None,
    notify: bool = False,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Log money actually sent to the player against a staff cashout record, as
    the dashboard "Add money sent" form does. `amount` is dollars (oversending is
    allowed and makes the record oversent); `method_display_name` is how it was
    sent (Venmo, Zelle, Cash App, PayPal, Crypto, Chips, ...); `sender_name` is
    the staff member who sent it (default: your token name). `proof_link` is only
    accepted for crypto methods (http/https transaction link); screenshot proofs
    stay on the dashboard. `notify=true` (dashboard default off) queues a
    "Sent $X!" message to the player's group from the club account via the bot
    worker, but only when the record is chat_connected. Sends feed the money-sent
    ledger, balance cards and weekly audits, recompute remaining/status, and once
    the amount is covered the worker clears the owed pin. Without confirm=true
    returns a dry run."""
    from bot.services.staff_cashout_records import (
        add_staff_cashout_send,
        record_chat_connected,
    )

    actor = actor_from_context(ctx)
    value = _positive_amount(amount)
    display = (method_display_name or "").strip()
    if not display:
        raise bad_input("method_display_name is required")
    sender = (sender_name or "").strip() or actor
    link = (proof_link or "").strip() or None
    before = _load_record(record_id)
    connected = record_chat_connected(before.get("club_id"), before.get("chat_id"))
    pdata = {
        "sender_name": sender,
        "amount": value,
        "payment_method_id": None,
        "payment_sub_option_id": None,
        "method_display_name": display,
        "notify_player": bool(notify),
        "proof_link": link,
        "proof": None,
    }
    validated = _validate_destination({"method_display_name": display})
    sent_after = Decimal(str(before.get("sent") or 0)) + value
    proposed = {
        "sender_name": sender,
        "amount": value,
        "method_display_name": validated["method_display_name"],
        "proof_link": link,
        "notify_player": bool(notify) and connected,
        "sent_after": sent_after,
        "remaining_after": Decimal(str(before.get("amount") or 0)) - sent_after,
    }
    notes = [_DOLLARS, _SEND_LEDGER_NOTE, _OWED_PIN_NOTE, _PLAYER_NOTIFY_NOTE]
    if notify and not connected:
        notes.append("notify ignored: this record is not connected to a group chat")
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed=proposed,
            message=(
                f"dry run: would log {value} sent via {display} on cashout record"
                f" {record_id}"
            ),
            notes=notes,
        )
    after = add_staff_cashout_send(int(record_id), pdata)
    if after is None:
        raise not_found(f"cashout record {record_id}")
    after = _compact_record(after)
    return write_result(
        actor=actor,
        before=before,
        after=after,
        message=(
            f"logged {value} sent via {display} on cashout record {record_id};"
            f" status is now {after.get('status')}"
        ),
        notes=notes,
    )


@translate_errors
def update_cashout_send(
    record_id: int,
    send_id: int,
    amount: float | None = None,
    method_display_name: str | None = None,
    sender_name: str | None = None,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Edit one money-sent row of a staff cashout record (dashboard "Edit money
    sent"): `amount` (dollars), `method_display_name` or `sender_name`. Only the
    fields you pass change; remaining/status are recomputed and a pending owed-pin
    clear is queued or withdrawn accordingly. Proof links and screenshots cannot
    be changed here. Without confirm=true returns a dry run with the current send
    row."""
    from bot.services.staff_cashout_records import update_staff_cashout_send

    pdata: dict[str, Any] = {}
    if amount is not None:
        pdata["amount"] = _positive_amount(amount)
    if method_display_name is not None:
        display = method_display_name.strip()
        if not display:
            raise bad_input("method_display_name must not be blank")
        pdata["payment_method_id"] = None
        pdata["payment_sub_option_id"] = None
        pdata["method_display_name"] = display
    if sender_name is not None:
        sender = sender_name.strip()
        if not sender:
            raise bad_input("sender_name must not be blank")
        pdata["sender_name"] = sender
    if not pdata:
        raise bad_input(
            "provide at least one of amount, method_display_name, sender_name"
        )
    actor = actor_from_context(ctx)
    before = _load_record(record_id)
    existing = _find_child(before, "sends", send_id)
    proposed = {
        k: v
        for k, v in pdata.items()
        if k not in ("payment_method_id", "payment_sub_option_id")
    }
    notes = [_DOLLARS, _SEND_LEDGER_NOTE, _OWED_PIN_NOTE]
    if not confirm:
        return dry_run(
            actor=actor,
            current={"record": _record_summary(before), "send": existing},
            proposed=proposed,
            message=(
                f"dry run: would update send {send_id} on cashout record {record_id}:"
                f" {', '.join(proposed)}"
            ),
            notes=notes,
        )
    after = update_staff_cashout_send(int(record_id), int(send_id), pdata)
    if after is None:
        raise not_found(f"send {send_id} on cashout record {record_id}")
    after = _compact_record(after)
    return write_result(
        actor=actor,
        before=before,
        after=after,
        message=(
            f"updated send {send_id} on cashout record {record_id}:"
            f" {', '.join(proposed)}; status is now {after.get('status')}"
        ),
        notes=notes,
    )


@translate_errors
def remove_cashout_send(
    record_id: int,
    send_id: int,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Remove one money-sent row from a staff cashout record (dashboard "Remove
    money sent"), including any attached proof. Remaining/status are recomputed
    (the record may become active again) and a still-pending owed-pin clear is
    withdrawn; no notification is sent. Without confirm=true returns a dry run
    with the full send row that would be removed."""
    from bot.services.staff_cashout_records import delete_staff_cashout_send

    actor = actor_from_context(ctx)
    before = _load_record(record_id)
    existing = _find_child(before, "sends", send_id)
    notes = [_DOLLARS, _SEND_LEDGER_NOTE]
    if not confirm:
        return dry_run(
            actor=actor,
            current={"record": _record_summary(before), "send": existing},
            proposed={"delete": True},
            message=(
                f"dry run: would remove the {existing.get('amount')} send {send_id}"
                f" from cashout record {record_id}"
            ),
            notes=notes,
        )
    after = delete_staff_cashout_send(int(record_id), int(send_id))
    if after is None:
        raise not_found(f"send {send_id} on cashout record {record_id}")
    after = _compact_record(after)
    return write_result(
        actor=actor,
        before=before,
        after=after,
        message=(
            f"removed send {send_id} ({existing.get('amount')}) from cashout record"
            f" {record_id}; status is now {after.get('status')}"
        ),
        notes=notes,
    )


def register_writes(mcp: MCPServer) -> None:
    mcp.tool(name="create_cashout_record", annotations=WRITE_NON_IDEMPOTENT)(
        create_cashout_record
    )
    mcp.tool(name="update_cashout_record", annotations=WRITE)(update_cashout_record)
    mcp.tool(name="delete_cashout_record", annotations=DESTRUCTIVE)(
        delete_cashout_record
    )
    mcp.tool(name="add_cashout_destination", annotations=WRITE_NON_IDEMPOTENT)(
        add_cashout_destination
    )
    mcp.tool(name="update_cashout_destination", annotations=WRITE)(
        update_cashout_destination
    )
    mcp.tool(name="remove_cashout_destination", annotations=DESTRUCTIVE)(
        remove_cashout_destination
    )
    mcp.tool(name="add_cashout_send", annotations=WRITE_NON_IDEMPOTENT)(
        add_cashout_send
    )
    mcp.tool(name="update_cashout_send", annotations=WRITE)(update_cashout_send)
    mcp.tool(name="remove_cashout_send", annotations=DESTRUCTIVE)(remove_cashout_send)
