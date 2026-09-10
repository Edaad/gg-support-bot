"""Manual trade-request deposit queue API."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session, joinedload

from api.auth import get_current_admin
from api.payments_helpers import owner_payment_search_clause
from bot.services.deposit_union_types import validate_deposit_union
from bot.services.pool_pay_types import pool_pay_type_from_method, validate_pool_pay_type
from bot.services.manual_deposit_requests import (
    ManualDepositCapacityError,
    ManualDepositValidationError,
    create_dashboard_manual_deposit_request,
    method_club_ids,
    union_deposit_slack_variant,
    update_dashboard_manual_deposit_request,
)
from bot.services.union_method_types import (
    union_type_display_name,
    union_type_from_display_name,
    validate_union_method_type,
)
from db.connection import get_db_dependency
from db.models import Club, ClubPaymentMethod, Group, ManualDepositRequest

router = APIRouter(
    prefix="/api",
    tags=["manual-deposit-requests"],
    dependencies=[Depends(get_current_admin)],
)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
_GROUP_SEARCH_LIMIT = 50


class ManualDepositRequestClubRead(BaseModel):
    id: int
    name: str


class ManualDepositRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    club_id: int
    method_id: Optional[int] = None
    method_name: str
    method_slug: str
    variant_name: str
    group_title: Optional[str] = None
    telegram_chat_id: int
    amount: Decimal
    trade_record_checked: bool
    source: str
    created_at: datetime
    club: Optional[ManualDepositRequestClubRead] = None


class ManualDepositRequestListResponse(BaseModel):
    items: List[ManualDepositRequestRead]
    total: int
    limit: int
    offset: int
    summary: "ManualDepositRequestSummary"


class ManualDepositRequestSummary(BaseModel):
    total_count: int
    total_amount: Decimal


class ManualDepositVariantListResponse(BaseModel):
    items: List[str]


class ManualDepositRequestCreate(BaseModel):
    amount: Decimal = Field(..., gt=0)
    telegram_chat_id: int
    created_at: Optional[datetime] = None
    trade_record_checked: bool = False


class ManualDepositRequestUpdate(BaseModel):
    amount: Optional[Decimal] = Field(None, gt=0)
    telegram_chat_id: Optional[int] = None
    created_at: Optional[datetime] = None
    trade_record_checked: Optional[bool] = None


class DepositGroupRead(BaseModel):
    chat_id: int
    name: Optional[str] = None
    club_id: int
    club_name: str


class DepositGroupListResponse(BaseModel):
    items: List[DepositGroupRead]


def _union_player_search_clause(session: Session, chat_id_column, term: str):
    """Player/group search for union manual deposits (PostgreSQL only)."""
    bind = session.get_bind()
    if bind is not None and bind.dialect.name != "postgresql":
        return None
    return owner_payment_search_clause(chat_id_column, term)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid datetime: {value}") from exc


def _to_read(row: ManualDepositRequest) -> ManualDepositRequestRead:
    club = None
    if row.club is not None:
        club = ManualDepositRequestClubRead(id=int(row.club.id), name=row.club.name)
    return ManualDepositRequestRead(
        id=int(row.id),
        club_id=int(row.club_id),
        method_id=int(row.method_id) if row.method_id is not None else None,
        method_name=row.method_name,
        method_slug=row.method_slug,
        variant_name=row.variant_name,
        group_title=row.group_title,
        telegram_chat_id=int(row.telegram_chat_id),
        amount=Decimal(str(row.amount)),
        trade_record_checked=bool(row.trade_record_checked),
        source=str(getattr(row, "source", None) or "bot"),
        created_at=row.created_at,
        club=club,
    )


def _get_manual_method(db: Session, method_id: int) -> ClubPaymentMethod:
    method = db.query(ClubPaymentMethod).get(int(method_id))
    if not method:
        raise HTTPException(404, "Method not found")
    if not bool(getattr(method, "tracks_manual_requests", False)):
        raise HTTPException(400, "Method is not a manual trade-request method")
    return method


def _http_error_from_service(exc: Exception) -> HTTPException:
    if isinstance(exc, ManualDepositCapacityError):
        return HTTPException(400, str(exc))
    if isinstance(exc, ManualDepositValidationError):
        msg = str(exc)
        if msg == "Request not found.":
            return HTTPException(404, msg)
        return HTTPException(400, msg)
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    raise exc


def _list_query(
    db: Session,
    *,
    club_id: Optional[int] = None,
    method_id: Optional[int] = None,
    method_slug: Optional[str] = None,
    method_type: Optional[str] = None,
    deposit_union: Optional[str] = None,
    pool_pay_type: Optional[str] = None,
    trade_record_checked: Optional[bool] = None,
    include_inactive_methods: bool = True,
    variant: Optional[str] = None,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    q: Optional[str] = None,
):
    query = db.query(ManualDepositRequest).options(
        joinedload(ManualDepositRequest.club),
    )
    if club_id is not None:
        query = query.filter(ManualDepositRequest.club_id == int(club_id))
    if method_id is not None:
        query = query.filter(ManualDepositRequest.method_id == int(method_id))
    if method_slug:
        query = query.filter(
            ManualDepositRequest.method_slug == method_slug.strip().lower()
        )
    if method_type:
        display = union_type_display_name(validate_union_method_type(method_type))
        query = query.filter(ManualDepositRequest.method_name == display)
    method_joined = False
    if deposit_union or pool_pay_type:
        query = query.join(
            ClubPaymentMethod,
            ClubPaymentMethod.id == ManualDepositRequest.method_id,
        )
        method_joined = True
        if deposit_union:
            union_slug = validate_deposit_union(deposit_union)
            query = query.filter(ClubPaymentMethod.deposit_union == union_slug)
        if pool_pay_type:
            pay_type = validate_pool_pay_type(pool_pay_type)
            query = query.filter(ClubPaymentMethod.pool_pay_type == pay_type)
    if trade_record_checked is not None:
        query = query.filter(
            ManualDepositRequest.trade_record_checked.is_(bool(trade_record_checked))
        )
    if variant and variant.strip():
        query = query.filter(ManualDepositRequest.variant_name == variant.strip())
    if from_dt is not None:
        query = query.filter(ManualDepositRequest.created_at >= from_dt)
    if to_dt is not None:
        query = query.filter(ManualDepositRequest.created_at <= to_dt)
    if not include_inactive_methods and not method_joined:
        query = query.join(
            ClubPaymentMethod,
            ClubPaymentMethod.id == ManualDepositRequest.method_id,
            isouter=True,
        ).filter(
            (ManualDepositRequest.method_id.is_(None))
            | (ClubPaymentMethod.is_active.is_(True))
        )
    elif not include_inactive_methods:
        query = query.filter(
            (ManualDepositRequest.method_id.is_(None))
            | (ClubPaymentMethod.is_active.is_(True))
        )
    search = (q or "").strip()
    if search:
        pattern = f"%{search}%"
        clauses = [
            ManualDepositRequest.group_title.ilike(pattern),
            Club.name.ilike(pattern),
            ManualDepositRequest.method_slug.ilike(pattern),
            ManualDepositRequest.variant_name.ilike(pattern),
            cast(ManualDepositRequest.amount, String).ilike(pattern),
        ]
        player_clause = _union_player_search_clause(
            db, ManualDepositRequest.telegram_chat_id, search
        )
        if player_clause is not None:
            clauses.append(player_clause)
        try:
            amount = Decimal(search)
        except (InvalidOperation, ValueError):
            amount = None
        if amount is not None:
            clauses.append(ManualDepositRequest.amount == amount)
        query = query.outerjoin(Club, Club.id == ManualDepositRequest.club_id).filter(
            or_(*clauses)
        )
    return query


def _apply_list_order(query):
    return query.order_by(
        ManualDepositRequest.created_at.desc(), ManualDepositRequest.id.desc()
    )


def _list_summary(query) -> ManualDepositRequestSummary:
    row = (
        query.order_by(None)
        .enable_eagerloads(False)
        .with_entities(
            func.count().label("total_count"),
            func.coalesce(func.sum(ManualDepositRequest.amount), 0).label("total_amount"),
        )
        .one()
    )
    return ManualDepositRequestSummary(
        total_count=int(row.total_count or 0),
        total_amount=Decimal(str(row.total_amount or 0)),
    )


@router.get(
    "/manual-deposit-requests/variants",
    response_model=ManualDepositVariantListResponse,
)
def list_manual_deposit_request_variants(
    club_id: Optional[int] = None,
    method_id: Optional[int] = None,
    method_slug: Optional[str] = None,
    type: Optional[str] = Query(None, alias="type"),
    method_type: Optional[str] = Query(None),
    deposit_union: Optional[str] = Query(None),
    pool_pay_type: Optional[str] = Query(None),
    trade_record_checked: Optional[bool] = None,
    include_inactive_methods: bool = Query(True),
    from_dt: Optional[str] = Query(None, alias="from"),
    to_dt: Optional[str] = Query(None, alias="to"),
    q: Optional[str] = Query(None),
    db: Session = Depends(get_db_dependency),
):
    resolved_type = type or method_type
    if resolved_type:
        validate_union_method_type(resolved_type)
    if deposit_union:
        validate_deposit_union(deposit_union)
    if pool_pay_type:
        validate_pool_pay_type(pool_pay_type)
    query = _list_query(
        db,
        club_id=club_id,
        method_id=method_id,
        method_slug=method_slug,
        method_type=resolved_type,
        deposit_union=deposit_union,
        pool_pay_type=pool_pay_type,
        trade_record_checked=trade_record_checked,
        include_inactive_methods=include_inactive_methods,
        from_dt=_parse_dt(from_dt),
        to_dt=_parse_dt(to_dt),
        q=q,
    )
    rows = (
        query.with_entities(ManualDepositRequest.variant_name)
        .distinct()
        .order_by(ManualDepositRequest.variant_name.asc())
        .all()
    )
    return ManualDepositVariantListResponse(
        items=[str(row[0]) for row in rows if row[0]]
    )


@router.get(
    "/manual-deposit-requests",
    response_model=ManualDepositRequestListResponse,
)
def list_manual_deposit_requests(
    club_id: Optional[int] = None,
    method_id: Optional[int] = None,
    method_slug: Optional[str] = None,
    type: Optional[str] = Query(None, alias="type"),
    method_type: Optional[str] = Query(None),
    deposit_union: Optional[str] = Query(None),
    pool_pay_type: Optional[str] = Query(None),
    trade_record_checked: Optional[bool] = None,
    include_inactive_methods: bool = Query(True),
    variant: Optional[str] = Query(None),
    from_dt: Optional[str] = Query(None, alias="from"),
    to_dt: Optional[str] = Query(None, alias="to"),
    q: Optional[str] = Query(None),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db_dependency),
):
    resolved_type = type or method_type
    if resolved_type:
        validate_union_method_type(resolved_type)
    if deposit_union:
        validate_deposit_union(deposit_union)
    if pool_pay_type:
        validate_pool_pay_type(pool_pay_type)
    query = _list_query(
        db,
        club_id=club_id,
        method_id=method_id,
        method_slug=method_slug,
        method_type=resolved_type,
        deposit_union=deposit_union,
        pool_pay_type=pool_pay_type,
        trade_record_checked=trade_record_checked,
        include_inactive_methods=include_inactive_methods,
        variant=variant,
        from_dt=_parse_dt(from_dt),
        to_dt=_parse_dt(to_dt),
        q=q,
    )
    summary = _list_summary(query)
    total = summary.total_count
    rows = _apply_list_order(query).offset(offset).limit(limit).all()
    return ManualDepositRequestListResponse(
        items=[_to_read(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
        summary=summary,
    )


@router.get(
    "/v2/methods/{method_id}/deposit-groups",
    response_model=DepositGroupListResponse,
)
def list_method_deposit_groups(
    method_id: int,
    q: Optional[str] = Query(None),
    limit: int = Query(_GROUP_SEARCH_LIMIT, ge=1, le=_GROUP_SEARCH_LIMIT),
    db: Session = Depends(get_db_dependency),
):
    _get_manual_method(db, int(method_id))
    allowed = method_club_ids(db, int(method_id))
    if not allowed:
        return DepositGroupListResponse(items=[])

    query = (
        db.query(Group, Club.name)
        .join(Club, Club.id == Group.club_id)
        .filter(Group.club_id.in_(allowed))
        .order_by(Group.name.asc(), Group.chat_id.asc())
    )
    search = (q or "").strip()
    if search:
        pattern = f"%{search}%"
        clauses = [Group.name.ilike(pattern)]
        if search.isdigit():
            clauses.append(Group.chat_id == int(search))
        query = query.filter(or_(*clauses))

    rows = query.limit(int(limit)).all()
    items = [
        DepositGroupRead(
            chat_id=int(group.chat_id),
            name=group.name,
            club_id=int(group.club_id),
            club_name=club_name or f"Club {group.club_id}",
        )
        for group, club_name in rows
    ]
    return DepositGroupListResponse(items=items)


@router.get(
    "/v2/methods/{method_id}/manual-deposit-requests",
    response_model=ManualDepositRequestListResponse,
)
def list_method_manual_deposit_requests(
    method_id: int,
    trade_record_checked: Optional[bool] = None,
    q: Optional[str] = Query(None),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db_dependency),
):
    _get_manual_method(db, int(method_id))
    query = _list_query(
        db,
        method_id=int(method_id),
        trade_record_checked=trade_record_checked,
        include_inactive_methods=True,
        q=q,
    )
    summary = _list_summary(query)
    total = summary.total_count
    rows = _apply_list_order(query).offset(offset).limit(limit).all()
    return ManualDepositRequestListResponse(
        items=[_to_read(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
        summary=summary,
    )


@router.post(
    "/v2/methods/{method_id}/manual-deposit-requests",
    response_model=ManualDepositRequestRead,
    status_code=201,
)
async def create_method_manual_deposit_request(
    method_id: int,
    body: ManualDepositRequestCreate,
    db: Session = Depends(get_db_dependency),
):
    method = _get_manual_method(db, int(method_id))
    union_type_slug = union_type_from_display_name(method.name or "")
    slack_variant = None
    method_display_name = (method.name or "").strip()
    if union_type_slug:
        try:
            slack_variant = union_deposit_slack_variant(
                int(body.telegram_chat_id),
                union_type_slug=union_type_slug,
            )
            method_display_name = union_type_display_name(union_type_slug)
        except Exception:
            slack_variant = "first"

    created_at = body.created_at
    if created_at is not None and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    try:
        row = create_dashboard_manual_deposit_request(
            method_id=int(method_id),
            amount=body.amount,
            telegram_chat_id=int(body.telegram_chat_id),
            created_at=created_at,
            trade_record_checked=bool(body.trade_record_checked),
        )
    except (ManualDepositCapacityError, ManualDepositValidationError, ValueError) as exc:
        raise _http_error_from_service(exc) from exc

    if slack_variant is not None or pool_pay_type_from_method(method) == "large_cashout":
        try:
            from bot.services.escalation_notification import (
                notify_pool_pay_deposit_slack,
            )

            await notify_pool_pay_deposit_slack(
                pool_pay_type=pool_pay_type_from_method(method),
                variant=slack_variant or "first",
                club_id=int(row.club_id),
                chat_id=int(row.telegram_chat_id),
                title=row.group_title,
                amount=row.amount,
                method_display_name=method_display_name,
                method_tag=(getattr(method, "method_tag", None) or "").strip() or None,
                requested_at=row.created_at,
            )
        except Exception:
            pass

    refreshed = (
        db.query(ManualDepositRequest)
        .options(joinedload(ManualDepositRequest.club))
        .filter(ManualDepositRequest.id == int(row.id))
        .first()
    )
    return _to_read(refreshed or row)


@router.patch(
    "/manual-deposit-requests/{request_id}",
    response_model=ManualDepositRequestRead,
)
def update_manual_deposit_request(
    request_id: int,
    body: ManualDepositRequestUpdate,
    db: Session = Depends(get_db_dependency),
):
    fields_set = body.model_fields_set
    if not fields_set:
        raise HTTPException(400, "No fields to update.")

    created_at = body.created_at
    if created_at is not None and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    try:
        row = update_dashboard_manual_deposit_request(
            request_id=int(request_id),
            amount=body.amount if "amount" in fields_set else None,
            telegram_chat_id=(
                body.telegram_chat_id if "telegram_chat_id" in fields_set else None
            ),
            created_at=created_at if "created_at" in fields_set else None,
            trade_record_checked=(
                body.trade_record_checked if "trade_record_checked" in fields_set else None
            ),
        )
    except (ManualDepositCapacityError, ManualDepositValidationError, ValueError) as exc:
        raise _http_error_from_service(exc) from exc

    refreshed = (
        db.query(ManualDepositRequest)
        .options(joinedload(ManualDepositRequest.club))
        .filter(ManualDepositRequest.id == int(row.id))
        .first()
    )
    return _to_read(refreshed or row)


@router.delete("/manual-deposit-requests/{request_id}", status_code=204)
def delete_manual_deposit_request(
    request_id: int,
    db: Session = Depends(get_db_dependency),
):
    row = db.query(ManualDepositRequest).get(int(request_id))
    if not row:
        raise HTTPException(404, "Request not found")
    db.delete(row)
    db.flush()
