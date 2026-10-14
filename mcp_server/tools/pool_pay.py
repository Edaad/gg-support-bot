"""Pool Pay tools: manual (trade-request) deposit requests and shared union methods.

Read tools plus confirm-gated writes (create/update/delete manual deposit requests).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, joinedload

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
from bot.services.manual_deposit_requests import (
    ManualDepositCapacityError,
    ManualDepositValidationError,
    method_club_ids,
)
from bot.services.pool_pay_types import (
    pool_pay_type_from_method,
    validate_pool_pay_type,
)
from bot.services.union_method_types import (
    union_type_display_name,
    union_type_from_display_name,
    validate_union_method_type,
)
from db.models import (
    ClubPaymentMethod,
    ClubPaymentMethodClub,
    Group,
    ManualDepositRequest,
)
from mcp_server.dates import parse_range, parse_when
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
    # write tools are registered by register_writes


# ── Write tools ──────────────────────────────────────────────────────────────

_REQUEST_NOTES = ("amounts are dollars",)


def _request_snapshot(request_id: int) -> dict[str, Any]:
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


def _service_error(exc: Exception) -> ToolError:
    """Map bot.services.manual_deposit_requests errors like the dashboard does."""
    message = str(exc)
    if (
        isinstance(exc, ManualDepositValidationError)
        and message == "Request not found."
    ):
        return not_found("manual deposit request")
    if isinstance(exc, (ManualDepositCapacityError, ManualDepositValidationError)):
        return bad_input(message)
    if isinstance(exc, ValueError):
        return bad_input(message)
    raise exc


def _manual_method(session: Session, method_id: int) -> ClubPaymentMethod:
    method = session.get(ClubPaymentMethod, int(method_id))
    if method is None:
        raise not_found(f"payment method {method_id}")
    if not bool(getattr(method, "tracks_manual_requests", False)):
        raise bad_input(
            f"method {method_id} ({method.name}) is not a Pool Pay manual"
            " trade-request method"
        )
    return method


def _resolve_deposit_group(
    session: Session,
    *,
    method_id: int,
    telegram_chat_id: int | None,
    group_title: str | None,
) -> Group:
    """The group a request is for, limited to the method's member clubs (same
    rule as the dashboard's deposit-group picker)."""
    allowed = method_club_ids(session, int(method_id))
    if not allowed:
        raise bad_input(f"method {method_id} has no club membership configured")
    base = session.query(Group).filter(Group.club_id.in_(allowed))
    if telegram_chat_id is not None:
        group = base.filter(Group.chat_id == int(telegram_chat_id)).one_or_none()
        if group is None:
            raise not_found(
                f"group chat {telegram_chat_id} among this method's clubs"
                f" (club ids {sorted(allowed)})"
            )
        return group
    title = (group_title or "").strip()
    if not title:
        raise bad_input("provide telegram_chat_id or group_title")
    exact = base.filter(func.lower(Group.name) == title.lower()).all()
    if len(exact) == 1:
        return exact[0]
    matches = exact or base.filter(Group.name.ilike(f"%{title}%")).limit(6).all()
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise not_found(f"no linked group titled {title!r} in this method's clubs")
    options = "; ".join(f"{g.name} (chat {int(g.chat_id)})" for g in matches[:5])
    raise bad_input(
        f"group_title {title!r} matches several groups, pass telegram_chat_id: {options}"
    )


def _slack_plan(method: ClubPaymentMethod) -> tuple[str | None, bool]:
    """(union type slug, whether the dashboard create would post to Slack)."""
    union_type_slug = union_type_from_display_name(method.name or "")
    posts = union_type_slug is not None or (
        pool_pay_type_from_method(method) == "large_cashout"
    )
    return union_type_slug, posts


@translate_errors
async def create_manual_deposit_request(
    method_id: int,
    amount: float,
    telegram_chat_id: int | None = None,
    group_title: str | None = None,
    created_at: str | None = None,
    trade_record_checked: bool = False,
    notify_slack: bool = True,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Add a Pool Pay manual deposit request against a shared union method
    (list_union_methods), as the dashboard "Add deposit" form does: the player's
    support group is given by `telegram_chat_id` or `group_title` and must belong
    to one of the method's member clubs. `amount` is dollars; `created_at` is an
    ISO datetime (naive = UTC, not in the future; default now). Min/max and the
    pool capacity limit are NOT enforced (same as the dashboard). For union
    methods (Zelle/Cash App/Apple Pay/Venmo) and large-cashout pools the
    dashboard posts a Slack deposit alert; set notify_slack=false to skip it.
    Without confirm=true returns a dry run of the row that would be created."""
    from bot.services.manual_deposit_requests import (
        create_dashboard_manual_deposit_request,
        union_deposit_slack_variant,
    )

    if amount is None or float(amount) <= 0:
        raise bad_input("amount must be greater than zero")
    when = parse_when(created_at)
    if when is not None and when > datetime.now(timezone.utc):
        raise bad_input("created_at cannot be in the future")
    actor = actor_from_context(ctx)
    with read_session() as session:
        method = _manual_method(session, method_id)
        group = _resolve_deposit_group(
            session,
            method_id=int(method_id),
            telegram_chat_id=telegram_chat_id,
            group_title=group_title,
        )
        names = club_name_map(session)
        union_type_slug, slack_posts = _slack_plan(method)
        will_notify = bool(notify_slack) and slack_posts
        method_display_name = (
            union_type_display_name(union_type_slug)
            if union_type_slug
            else (method.name or "").strip()
        )
        method_tag = (getattr(method, "method_tag", None) or "").strip() or None
        deposit_union = getattr(method, "deposit_union", None)
        pool_pay_type = pool_pay_type_from_method(method)
        chat_id = int(group.chat_id)
        title = (group.name or "").strip()[:512] or None
        club_id = int(group.club_id)
        proposed = {
            "method_id": int(method.id),
            "method_name": method_display_name,
            "method_slug": (method.slug or "").strip().lower(),
            "variant_name": method_tag or "default",
            "club_id": club_id,
            "club_name": names.get(club_id),
            "telegram_chat_id": chat_id,
            "group_title": title,
            "amount": round(float(amount), 2),
            "trade_record_checked": bool(trade_record_checked),
            "created_at": when,
            "source": "dashboard",
            "slack_notification": will_notify,
        }
    notes = [
        *_REQUEST_NOTES,
        "min/max and the pool capacity limit are not enforced (dashboard behaviour)",
        "posts a Slack deposit alert"
        if will_notify
        else "no Slack notification"
        + ("" if slack_posts else " (method type never alerts)"),
    ]
    if not confirm:
        return dry_run(
            actor=actor,
            current=None,
            proposed=proposed,
            message="dry run: would create this manual deposit request",
            notes=notes,
        )
    slack_variant: str | None = None
    if union_type_slug:
        try:
            slack_variant = union_deposit_slack_variant(
                chat_id, union_type_slug=union_type_slug
            )
        except Exception:
            slack_variant = "first"
    try:
        row = create_dashboard_manual_deposit_request(
            method_id=int(method_id),
            amount=Decimal(str(amount)),
            telegram_chat_id=chat_id,
            created_at=when,
            trade_record_checked=bool(trade_record_checked),
        )
    except (
        ManualDepositCapacityError,
        ManualDepositValidationError,
        ValueError,
    ) as exc:
        raise _service_error(exc) from exc
    if will_notify:
        from bot.services.escalation_notification import notify_pool_pay_deposit_slack

        try:
            posted = await notify_pool_pay_deposit_slack(
                pool_pay_type=pool_pay_type,
                variant=slack_variant or "first",
                club_id=int(row.club_id),
                chat_id=int(row.telegram_chat_id),
                title=row.group_title,
                amount=row.amount,
                method_display_name=method_display_name,
                method_tag=method_tag,
                requested_at=row.created_at,
                deposit_union=deposit_union,
            )
        except Exception:
            posted = False
        notes[-1] = (
            "Slack deposit alert posted"
            if posted
            else "Slack deposit alert was not posted (ignored, as the dashboard does)"
        )
    after = _request_snapshot(int(row.id))
    return write_result(
        actor=actor,
        before=None,
        after=after,
        message=f"created manual deposit request {after['id']} for {title}",
        notes=notes,
    )


@translate_errors
def update_manual_deposit_request(
    request_id: int,
    trade_record_checked: bool | None = None,
    amount: float | None = None,
    telegram_chat_id: int | None = None,
    created_at: str | None = None,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Edit a Pool Pay manual deposit request as the dashboard PATCH does: mark
    it trade-record checked (or unchecked), correct its dollar `amount`, move it
    to another player's group (`telegram_chat_id`, must be in the method's clubs;
    group_title and club follow) or change `created_at` (ISO datetime, naive =
    UTC, not in the future). At least one field is required; no notifications.
    Without confirm=true returns a dry run with the current row and the change."""
    from bot.services.manual_deposit_requests import (
        update_dashboard_manual_deposit_request,
    )

    if (
        trade_record_checked is None
        and amount is None
        and telegram_chat_id is None
        and created_at is None
    ):
        raise bad_input(
            "provide at least one of trade_record_checked, amount, telegram_chat_id,"
            " created_at"
        )
    if amount is not None and amount <= 0:
        raise bad_input("amount must be greater than zero")
    when = parse_when(created_at)
    if created_at is not None and when is None:
        raise bad_input("created_at must be an ISO datetime")
    if when is not None and when > datetime.now(timezone.utc):
        raise bad_input("created_at cannot be in the future")
    actor = actor_from_context(ctx)
    before = _request_snapshot(request_id)
    proposed: dict[str, Any] = {}
    if trade_record_checked is not None:
        proposed["trade_record_checked"] = bool(trade_record_checked)
    if amount is not None:
        proposed["amount"] = round(float(amount), 2)
    if telegram_chat_id is not None:
        if before.get("method_id") is None:
            raise bad_input("request has no linked method; the group cannot be moved")
        with read_session() as session:
            group = _resolve_deposit_group(
                session,
                method_id=int(before["method_id"]),
                telegram_chat_id=int(telegram_chat_id),
                group_title=None,
            )
            names = club_name_map(session)
            proposed["telegram_chat_id"] = int(group.chat_id)
            proposed["group_title"] = (group.name or "").strip()[:512] or None
            proposed["club_id"] = int(group.club_id)
            proposed["club_name"] = names.get(int(group.club_id))
    if when is not None:
        proposed["created_at"] = when
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed=proposed,
            message="dry run: would update the manual deposit request",
            notes=list(_REQUEST_NOTES),
        )
    try:
        update_dashboard_manual_deposit_request(
            request_id=int(request_id),
            amount=Decimal(str(amount)) if amount is not None else None,
            telegram_chat_id=int(telegram_chat_id)
            if telegram_chat_id is not None
            else None,
            created_at=when,
            trade_record_checked=trade_record_checked,
        )
    except (
        ManualDepositCapacityError,
        ManualDepositValidationError,
        ValueError,
    ) as exc:
        raise _service_error(exc) from exc
    after = _request_snapshot(request_id)
    return write_result(
        actor=actor,
        before=before,
        after=after,
        message=f"updated manual deposit request {request_id}: {', '.join(proposed)}",
        notes=list(_REQUEST_NOTES),
    )


@translate_errors
def delete_manual_deposit_request(
    request_id: int, confirm: bool = False, ctx: Context | None = None
) -> dict[str, Any]:
    """Permanently delete a Pool Pay manual deposit request, as the dashboard
    delete does (this frees the amount from the pool's used total). No
    notifications. Without confirm=true returns a dry run showing the full row
    that would be removed."""
    actor = actor_from_context(ctx)
    before = _request_snapshot(request_id)
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed={"action": "delete"},
            message=f"dry run: would delete manual deposit request {request_id}",
            notes=list(_REQUEST_NOTES),
        )
    with write_session() as session:
        row = session.get(ManualDepositRequest, int(request_id))
        if row is None:
            raise not_found(f"manual deposit request {request_id}")
        session.delete(row)
        session.flush()
    return write_result(
        actor=actor,
        before=before,
        after=None,
        message=f"deleted manual deposit request {request_id}",
        notes=list(_REQUEST_NOTES),
    )


def register_writes(mcp: MCPServer) -> None:
    mcp.tool(name="create_manual_deposit_request", annotations=WRITE_NON_IDEMPOTENT)(
        create_manual_deposit_request
    )
    mcp.tool(name="update_manual_deposit_request", annotations=WRITE)(
        update_manual_deposit_request
    )
    mcp.tool(name="delete_manual_deposit_request", annotations=DESTRUCTIVE)(
        delete_manual_deposit_request
    )
