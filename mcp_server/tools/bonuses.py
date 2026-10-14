"""Bonus records issued to players and the admin-configured bonus types."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from bot.services.bonus_records import list_bonus_records as _list_bonus_records
from bot.services.bonus_records import record_to_dict
from bot.services.player_details import parse_tracking_title
from config import CLUB_SHORTHAND_TO_NAME
from db.models import BonusRecord, BonusType, Club, Group
from mcp_server.auth import actor_from_context
from mcp_server.dates import parse_when
from mcp_server.db import read_session, write_session
from mcp_server.envelope import dry_run, page, write_result
from mcp_server.errors import bad_input, not_found, translate_errors
from mcp_server.tools._common import (
    DESTRUCTIVE,
    READ_ONLY,
    WRITE,
    WRITE_NON_IDEMPOTENT,
    clamp_limit,
    club_name_map,
    resolve_club,
    resolve_club_id,
)


@translate_errors
def list_bonus_records(
    club: str | None = None,
    bonus_type_id: int | None = None,
    other: bool = False,
    q: str | None = None,
    limit: int = 50,
    include_pii: bool = False,
) -> dict[str, Any]:
    """Bonuses recorded for players (via /bonus or the dashboard), newest issued
    first. `bonus_type_id` keeps one type; `other=true` keeps free-text bonuses
    with no type; `q` matches group title, player name, gg_player_id or the custom
    description. Amounts are dollars. No offset paging: narrow with filters.
    Use list_bonus_types for type ids. Club: id, key (round_table|creator_club|
    clubgto) or slug (round-table|creator-club|clubgto|aces-table); omit for all."""
    limit = clamp_limit(limit)
    with read_session() as session:
        club_id = resolve_club_id(session, club)
        names = club_name_map(session)
    rows = _list_bonus_records(
        club_id=club_id,
        bonus_type_id=int(bonus_type_id) if bonus_type_id is not None else None,
        other=bool(other),
        q=q,
        limit=limit,
    )
    items = []
    for row in rows:
        data = dict(row)
        club_ref_id = data.get("club_id")
        if data.get("club_name") is None and club_ref_id is not None:
            data["club_name"] = names.get(int(club_ref_id))
        items.append(data)
    return page(
        items,
        total=None,
        limit=limit,
        offset=0,
        include_pii=include_pii,
        notes=[
            "no offset paging; narrow with club/bonus_type_id/q",
            "amounts are dollars",
            "player_username is the player's display name (masked unless include_pii)",
        ],
    )


@translate_errors
def list_bonus_types() -> dict[str, Any]:
    """Bonus categories offered in the /bonus flow (id, name, is_active,
    sort_order), in display order. Pass an id as bonus_type_id to
    list_bonus_records."""
    with read_session() as session:
        rows = (
            session.query(BonusType).order_by(BonusType.sort_order, BonusType.id).all()
        )
        items = [
            {
                "id": int(row.id),
                "name": row.name,
                "is_active": bool(row.is_active) if row.is_active is not None else True,
                "sort_order": row.sort_order,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    return page(items, total=len(items), limit=len(items) or 1, offset=0)


def register(mcp: MCPServer) -> None:
    mcp.tool(name="list_bonus_records", annotations=READ_ONLY)(list_bonus_records)
    mcp.tool(name="list_bonus_types", annotations=READ_ONLY)(list_bonus_types)
    # write tools are registered by register_writes


# ── Write tools ──────────────────────────────────────────────────────────────

_RECORD_NOTES = (
    "amounts are dollars",
    "nothing is posted to Telegram or Slack (same as the dashboard Bonuses page)",
)
_GG_PLAYER_ID_RE = re.compile(r"^[0-9]{1,48}-[0-9]{1,48}$")

# clubs.name -> the shorthand used in group titles ("GTO / 8190-5287 / name").
# CLUB_SHORTHAND_TO_NAME lists RT before AT, so Round Table maps to RT.
_SHORTHAND_FOR_CLUB_NAME: dict[str, str] = {}
for _shorthand, _club_name in CLUB_SHORTHAND_TO_NAME.items():
    _SHORTHAND_FOR_CLUB_NAME.setdefault(_club_name, _shorthand)


def _type_dict(row: BonusType) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "name": row.name,
        "is_active": bool(row.is_active) if row.is_active is not None else True,
        "sort_order": row.sort_order,
        "created_at": row.created_at,
    }


def _load_type(session: Session, bonus_type_id: int) -> BonusType:
    row = session.get(BonusType, int(bonus_type_id))
    if row is None:
        raise not_found(f"bonus type {bonus_type_id}")
    return row


def _type_name_taken(session: Session, name: str, exclude_id: int | None) -> str | None:
    query = session.query(BonusType).filter(BonusType.name.ilike(name))
    if exclude_id is not None:
        query = query.filter(BonusType.id != int(exclude_id))
    existing = query.first()
    return str(existing.name) if existing is not None else None


def _record_snapshot(record_id: int) -> dict[str, Any] | None:
    """Current record as the dashboard renders it (bot.services.bonus_records)."""
    with read_session() as session:
        row = (
            session.query(BonusRecord)
            .options(joinedload(BonusRecord.bonus_type), joinedload(BonusRecord.club))
            .filter(BonusRecord.id == int(record_id))
            .first()
        )
        if row is None:
            return None
        return record_to_dict(row)


def _type_for_proposal(
    session: Session, bonus_type_id: int | None, custom_description: str | None
) -> tuple[int | None, str | None, str | None]:
    """Mirror bot.services.bonus_records._validate_type for the dry run."""
    desc = (custom_description or "").strip() or None
    if bonus_type_id is None:
        if not desc:
            raise bad_input(
                "custom_description is required when no bonus_type_id is given (Other)"
            )
        return None, None, desc
    row = _load_type(session, bonus_type_id)
    if row.is_active is False:
        raise bad_input(f"bonus type {row.name!r} is not active")
    return int(row.id), str(row.name), desc


def _resolve_group_title(
    session: Session,
    club_row: Club,
    group_title: str | None,
    gg_player_id: str | None,
) -> tuple[str, list[str]]:
    """Return the group title the record is keyed on plus notes for the caller.

    The dashboard form only takes a title; the bonus service parses the player
    (shorthand / gg_player_id / name) out of it. When only a gg_player_id is
    given we look for that player's linked support group in the club and fall
    back to a synthetic "SHORTHAND / gg_player_id" title.
    """
    notes: list[str] = []
    title = (group_title or "").strip()
    pid = (gg_player_id or "").strip()
    if pid and not _GG_PLAYER_ID_RE.match(pid):
        raise bad_input("gg_player_id must look like 8190-5287")
    if title:
        parsed = parse_tracking_title(title)
        if pid and (parsed is None or parsed[1] != pid):
            raise bad_input("group_title and gg_player_id name different players")
        if parsed is None:
            notes.append(
                "group_title does not parse as CLUB / PLAYER_ID / NAME; the record"
                " will be stored unresolved (player_resolved=false)"
            )
        return title, notes
    if not pid:
        raise bad_input("provide group_title or gg_player_id")
    candidates = (
        session.query(Group)
        .filter(Group.club_id == int(club_row.id), Group.name.ilike(f"%{pid}%"))
        .order_by(Group.added_at.desc(), Group.chat_id.desc())
        .all()
    )
    for group in candidates:
        parsed = parse_tracking_title(group.name or "")
        if parsed is not None and parsed[1] == pid:
            notes.append(
                f"group_title resolved from linked group chat {int(group.chat_id)}"
            )
            return str(group.name).strip(), notes
    shorthand = _SHORTHAND_FOR_CLUB_NAME.get(str(club_row.name))
    if shorthand is None:
        raise bad_input(
            f"no group title shorthand is known for club {club_row.name!r};"
            " pass group_title instead"
        )
    notes.append(
        "no linked support group found for this gg_player_id; using a synthetic"
        f" title {shorthand} / {pid}"
    )
    return f"{shorthand} / {pid}", notes


@translate_errors
def create_bonus_record(
    club: str,
    amount: float,
    group_title: str | None = None,
    gg_player_id: str | None = None,
    bonus_type_id: int | None = None,
    custom_description: str | None = None,
    issued_at: str | None = None,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Record a bonus (dollars) for a player, as the dashboard Bonuses "Add"
    form does. Identify the player by `group_title` ("GTO / 8190-5287 / name",
    the linked support group title) or by `gg_player_id` (the player's linked
    group in `club` is looked up; without one a "SHORTHAND / id" title is used).
    `bonus_type_id` is a type from list_bonus_types (must be active); omit it
    for an "Other" bonus, which then requires `custom_description`. `issued_at`
    is an ISO datetime (naive = UTC; default now). Nothing is posted to Telegram
    or Slack. Without confirm=true returns a dry run of the row to be created."""
    from bot.services.bonus_records import create_bonus_record as svc_create

    if amount is None or float(amount) <= 0:
        raise bad_input("amount must be greater than zero")
    when = parse_when(issued_at)
    actor = actor_from_context(ctx)
    with read_session() as session:
        club_row = resolve_club(session, club)
        if club_row is None:
            raise bad_input("club is required")
        club_id, club_name = int(club_row.id), str(club_row.name)
        type_id, type_name, desc = _type_for_proposal(
            session, bonus_type_id, custom_description
        )
        title, notes = _resolve_group_title(
            session, club_row, group_title, gg_player_id
        )
    parsed = parse_tracking_title(title)
    proposed = {
        "club_id": club_id,
        "club_name": club_name,
        "group_title": title,
        "gg_player_id": parsed[1] if parsed else None,
        "amount": round(float(amount), 2),
        "bonus_type_id": type_id,
        "bonus_type_name": type_name,
        "custom_description": desc,
        "issued_at": when,
    }
    if not confirm:
        return dry_run(
            actor=actor,
            current=None,
            proposed=proposed,
            message="dry run: would create this bonus record",
            notes=[*_RECORD_NOTES, *notes],
        )
    after = svc_create(
        club_id=club_id,
        group_title=title,
        amount=Decimal(str(amount)),
        bonus_type_id=type_id,
        custom_description=desc,
        issued_at=when,
    )
    return write_result(
        actor=actor,
        before=None,
        after=after,
        message=f"created bonus record {after['id']} for {title} ({club_name})",
        notes=[*_RECORD_NOTES, *notes],
    )


@translate_errors
def update_bonus_record(
    record_id: int,
    club: str | None = None,
    group_title: str | None = None,
    amount: float | None = None,
    bonus_type_id: int | None = None,
    custom_description: str | None = None,
    clear_bonus_type: bool = False,
    issued_at: str | None = None,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Edit a bonus record like the dashboard PATCH: change `club`, `group_title`,
    `amount` (dollars), `bonus_type_id`, `custom_description` or `issued_at`
    (ISO datetime, naive = UTC). Pass clear_bonus_type=true to turn it into an
    "Other" bonus (then a custom_description must exist). Only the fields you
    pass change; the player (gg_player_id, chat) is re-resolved from the final
    group title. Without confirm=true returns a dry run with the current row."""
    from bot.services.bonus_records import update_bonus_record as svc_update

    if clear_bonus_type and bonus_type_id is not None:
        raise bad_input("pass either bonus_type_id or clear_bonus_type, not both")
    if amount is not None and float(amount) <= 0:
        raise bad_input("amount must be greater than zero")
    if group_title is not None and not group_title.strip():
        raise bad_input("group_title must not be blank")
    updates: dict[str, Any] = {}
    if group_title is not None:
        updates["group_title"] = group_title.strip()
    if amount is not None:
        updates["amount"] = Decimal(str(amount))
    if bonus_type_id is not None:
        updates["bonus_type_id"] = int(bonus_type_id)
    if clear_bonus_type:
        updates["bonus_type_id"] = None
    if custom_description is not None:
        updates["custom_description"] = custom_description.strip() or None
    if issued_at is not None:
        when = parse_when(issued_at)
        if when is None:
            raise bad_input("issued_at must be an ISO datetime")
        updates["issued_at"] = when
    actor = actor_from_context(ctx)
    before = _record_snapshot(record_id)
    if before is None:
        raise not_found(f"bonus record {record_id}")
    proposed: dict[str, Any] = {}
    with read_session() as session:
        if club is not None:
            club_row = resolve_club(session, club)
            if club_row is None:
                raise bad_input("club must not be blank")
            updates["club_id"] = int(club_row.id)
            proposed["club_name"] = str(club_row.name)
        if "bonus_type_id" in updates or "custom_description" in updates:
            type_id = updates.get("bonus_type_id", before.get("bonus_type_id"))
            desc = updates.get("custom_description", before.get("custom_description"))
            _type_id, type_name, _desc = _type_for_proposal(session, type_id, desc)
            proposed["bonus_type_name"] = type_name
    if not updates:
        raise bad_input(
            "provide at least one of club, group_title, amount, bonus_type_id,"
            " custom_description, clear_bonus_type, issued_at"
        )
    proposed.update(
        {k: (round(float(v), 2) if k == "amount" else v) for k, v in updates.items()}
    )
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed=proposed,
            message="dry run: would update the bonus record",
            notes=list(_RECORD_NOTES),
        )
    after = svc_update(int(record_id), **updates)
    if after is None:
        raise not_found(f"bonus record {record_id}")
    return write_result(
        actor=actor,
        before=before,
        after=after,
        message=f"updated bonus record {record_id}: {', '.join(updates)}",
        notes=list(_RECORD_NOTES),
    )


@translate_errors
def delete_bonus_record(
    record_id: int, confirm: bool = False, ctx: Context | None = None
) -> dict[str, Any]:
    """Permanently delete a bonus record, as the dashboard Bonuses delete does.
    Nothing is posted to Telegram or Slack. Without confirm=true returns a dry
    run showing the full row that would be removed."""
    from bot.services.bonus_records import delete_bonus_record as svc_delete

    actor = actor_from_context(ctx)
    before = _record_snapshot(record_id)
    if before is None:
        raise not_found(f"bonus record {record_id}")
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed={"action": "delete"},
            message=f"dry run: would delete bonus record {record_id}",
            notes=list(_RECORD_NOTES),
        )
    if not svc_delete(int(record_id)):
        raise not_found(f"bonus record {record_id}")
    return write_result(
        actor=actor,
        before=before,
        after=None,
        message=f"deleted bonus record {record_id}",
        notes=list(_RECORD_NOTES),
    )


@translate_errors
def create_bonus_type(
    name: str,
    sort_order: int = 0,
    is_active: bool = True,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Add a bonus category offered in the /bonus flow and the dashboard, as the
    Bonus types settings do. Names are unique (case-insensitive); `sort_order`
    orders the list. Without confirm=true returns a dry run."""
    clean = (name or "").strip()
    if not clean:
        raise bad_input("name is required")
    actor = actor_from_context(ctx)
    with read_session() as session:
        taken = _type_name_taken(session, clean, None)
    if taken is not None:
        raise ToolError(f"conflict: bonus type {taken!r} already exists")
    proposed = {
        "name": clean,
        "sort_order": int(sort_order),
        "is_active": bool(is_active),
    }
    if not confirm:
        return dry_run(
            actor=actor,
            current=None,
            proposed=proposed,
            message="dry run: would create this bonus type",
        )
    with write_session() as session:
        row = BonusType(
            name=clean, sort_order=int(sort_order), is_active=bool(is_active)
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        after = _type_dict(row)
    return write_result(
        actor=actor,
        before=None,
        after=after,
        message=f"created bonus type {after['id']} {clean!r}",
    )


@translate_errors
def update_bonus_type(
    bonus_type_id: int,
    name: str | None = None,
    is_active: bool | None = None,
    sort_order: int | None = None,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Rename, reorder or (de)activate a bonus type, as the dashboard PUT does.
    Inactive types stay on existing records but cannot be chosen for new ones.
    Only the fields you pass change. Without confirm=true returns a dry run."""
    if name is None and is_active is None and sort_order is None:
        raise bad_input("provide at least one of name, is_active, sort_order")
    clean = name.strip() if name is not None else None
    if name is not None and not clean:
        raise bad_input("name must not be blank")
    actor = actor_from_context(ctx)
    with read_session() as session:
        row = _load_type(session, bonus_type_id)
        before = _type_dict(row)
        if clean is not None:
            taken = _type_name_taken(session, clean, int(row.id))
            if taken is not None:
                raise ToolError(f"conflict: bonus type {taken!r} already exists")
    proposed = {
        k: v
        for k, v in {
            "name": clean,
            "is_active": is_active,
            "sort_order": int(sort_order) if sort_order is not None else None,
        }.items()
        if v is not None
    }
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed=proposed,
            message="dry run: would update the bonus type",
        )
    with write_session() as session:
        row = _load_type(session, bonus_type_id)
        for field, value in proposed.items():
            setattr(row, field, value)
        session.flush()
        session.refresh(row)
        after = _type_dict(row)
    return write_result(
        actor=actor,
        before=before,
        after=after,
        message=f"updated bonus type {bonus_type_id}: {', '.join(proposed)}",
    )


@translate_errors
def delete_bonus_type(
    bonus_type_id: int, confirm: bool = False, ctx: Context | None = None
) -> dict[str, Any]:
    """Permanently delete a bonus type, as the dashboard delete does. Bonus
    records that used it are kept but lose the type (they show as "Other");
    the dry run reports how many records that affects. Prefer
    update_bonus_type(is_active=false) to retire a type. Without confirm=true
    returns a dry run showing the row that would be removed."""
    actor = actor_from_context(ctx)
    with read_session() as session:
        row = _load_type(session, bonus_type_id)
        before = _type_dict(row)
        referencing = int(
            session.query(func.count(BonusRecord.id))
            .filter(BonusRecord.bonus_type_id == int(row.id))
            .scalar()
            or 0
        )
    notes = [
        f"{referencing} bonus record(s) reference this type and would keep their"
        " row with bonus_type_id=null"
    ]
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed={"action": "delete", "records_referencing": referencing},
            message=f"dry run: would delete bonus type {bonus_type_id}",
            notes=notes,
        )
    with write_session() as session:
        row = _load_type(session, bonus_type_id)
        session.delete(row)
        session.flush()
    return write_result(
        actor=actor,
        before=before,
        after=None,
        message=f"deleted bonus type {bonus_type_id} ({before['name']})",
        notes=notes,
    )


def register_writes(mcp: MCPServer) -> None:
    mcp.tool(name="create_bonus_record", annotations=WRITE_NON_IDEMPOTENT)(
        create_bonus_record
    )
    mcp.tool(name="update_bonus_record", annotations=WRITE)(update_bonus_record)
    mcp.tool(name="delete_bonus_record", annotations=DESTRUCTIVE)(delete_bonus_record)
    mcp.tool(name="create_bonus_type", annotations=WRITE_NON_IDEMPOTENT)(
        create_bonus_type
    )
    mcp.tool(name="update_bonus_type", annotations=WRITE)(update_bonus_type)
    mcp.tool(name="delete_bonus_type", annotations=DESTRUCTIVE)(delete_bonus_type)
