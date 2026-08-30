"""Union methods API: shared multi-club manual deposit configs (Pool Pay)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from api.auth import get_current_admin
from api.payment_v2_helpers import apply_manual_trade_request_constraints
from bot.services.deposit_union_types import validate_deposit_union
from bot.services.pool_pay_types import (
    build_pool_pay_slug,
    normalize_identifier_suffix,
    parse_pool_pay_slug,
    validate_pool_pay_type,
)
from bot.services.union_method_types import (
    union_type_display_name,
    union_type_from_display_name,
    validate_union_method_type,
)
from db.connection import get_db_dependency
from db.models import (
    Club,
    ClubPaymentMethod,
    ClubPaymentMethodClub,
    ManualDepositRequest,
)

router = APIRouter(
    prefix="/api/pool-pay",
    tags=["pool-pay"],
    dependencies=[Depends(get_current_admin)],
)

UnionMethodTypeSlug = Literal["zelle", "cashapp", "applepay", "venmo"]
DepositUnionSlug = Literal["tmt", "massiv"]
PoolPayTypeSlug = Literal["union_method", "large_cashout"]


class UnionMethodClubRead(BaseModel):
    id: int
    name: str


class UnionMethodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    pool_pay_type: PoolPayTypeSlug
    deposit_union: Optional[str] = None
    internal_identifier: str
    identifier_suffix: str
    method_tag: str
    payment_account_name: Optional[str] = None
    is_active: bool
    sort_order: int
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    deposit_limit: Decimal
    clubs: List[UnionMethodClubRead]
    row_clubs: List[UnionMethodClubRead] = Field(default_factory=list)
    used_sum: Decimal
    unchecked_count: int
    deposit_request_count: int


class UnionMethodCreate(BaseModel):
    pool_pay_type: PoolPayTypeSlug = "union_method"
    type: UnionMethodTypeSlug
    deposit_union: Optional[DepositUnionSlug] = None
    identifier_suffix: str = Field(..., min_length=1, max_length=50)
    method_tag: str = Field(..., min_length=1, max_length=200)
    payment_account_name: Optional[str] = Field(None, max_length=200)
    club_ids: List[int] = Field(..., min_length=1)
    deposit_limit: Decimal
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None

    @model_validator(mode="after")
    def _validate_deposit_union(self) -> "UnionMethodCreate":
        pay_type = validate_pool_pay_type(self.pool_pay_type)
        if pay_type == "union_method":
            if self.deposit_union is None:
                raise ValueError("deposit_union is required for union method pool pay.")
        elif self.deposit_union is not None:
            raise ValueError("deposit_union is not allowed for large cashout pool pay.")
        return self


class UnionMethodUpdate(BaseModel):
    pool_pay_type: Optional[PoolPayTypeSlug] = None
    type: Optional[UnionMethodTypeSlug] = None
    deposit_union: Optional[DepositUnionSlug] = None
    identifier_suffix: Optional[str] = Field(None, min_length=1, max_length=50)
    method_tag: Optional[str] = Field(None, min_length=1, max_length=200)
    payment_account_name: Optional[str] = Field(None, max_length=200)
    club_ids: Optional[List[int]] = Field(None, min_length=1)
    deposit_limit: Optional[Decimal] = None
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None


class UnionMethodReorderBody(BaseModel):
    type: UnionMethodTypeSlug
    order: List[int] = Field(..., min_length=1)


def _normalize_internal_identifier(value: str) -> str:
    return value.strip().lower()


def _union_internal_identifier_exists(
    db: Session, identifier: str, *, exclude_method_id: Optional[int] = None
) -> bool:
    q = db.query(ClubPaymentMethod.id).filter(
        ClubPaymentMethod.tracks_manual_requests.is_(True),
        ClubPaymentMethod.direction == "deposit",
        ClubPaymentMethod.slug == identifier,
    )
    if exclude_method_id is not None:
        q = q.filter(ClubPaymentMethod.id != int(exclude_method_id))
    return q.first() is not None


def _ensure_unique_internal_identifier(
    db: Session,
    base: str,
    *,
    exclude_method_id: Optional[int] = None,
) -> str:
    normalized = _normalize_internal_identifier(base)
    if not normalized:
        raise HTTPException(400, "Internal identifier is required.")
    if not _union_internal_identifier_exists(
        db, normalized, exclude_method_id=exclude_method_id
    ):
        return normalized
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    candidate = f"{normalized}-{stamp}"
    if not _union_internal_identifier_exists(
        db, candidate, exclude_method_id=exclude_method_id
    ):
        return candidate
    raise HTTPException(
        400, "Could not allocate a unique internal identifier; try a different value."
    )


def _build_slug_from_parts(
    *,
    method_type: str,
    pool_pay_type: str,
    identifier_suffix: str,
    db: Session,
    exclude_method_id: Optional[int] = None,
) -> str:
    try:
        suffix = normalize_identifier_suffix(identifier_suffix)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        slug = build_pool_pay_slug(method_type, pool_pay_type, suffix)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _ensure_unique_internal_identifier(
        db, slug, exclude_method_id=exclude_method_id
    )


def _identifier_suffix_for_method(method: ClubPaymentMethod) -> str:
    parsed = parse_pool_pay_slug(method.slug or "")
    if parsed:
        return parsed[2]
    return method.slug or ""


def _pool_pay_type_for_method(method: ClubPaymentMethod) -> str:
    raw = getattr(method, "pool_pay_type", None)
    if raw:
        try:
            return validate_pool_pay_type(str(raw))
        except ValueError:
            pass
    parsed = parse_pool_pay_slug(method.slug or "")
    if parsed:
        return parsed[1]
    return "union_method"


def _next_sort_order(db: Session, method_type: str) -> int:
    type_slug = validate_union_method_type(method_type)
    max_order = (
        db.query(func.coalesce(func.max(ClubPaymentMethod.sort_order), -1))
        .filter(
            ClubPaymentMethod.tracks_manual_requests.is_(True),
            ClubPaymentMethod.direction == "deposit",
            ClubPaymentMethod.union_type == type_slug,
        )
        .scalar()
    )
    return int(max_order) + 1


def _validate_club_ids(db: Session, club_ids: List[int]) -> List[Club]:
    ids = sorted({int(c) for c in club_ids})
    if not ids:
        raise HTTPException(400, "Select at least one club.")
    clubs = db.query(Club).filter(Club.id.in_(ids)).all()
    found = {int(c.id) for c in clubs}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(400, f"Unknown club id(s): {missing}")
    return sorted(clubs, key=lambda c: int(c.id))


def _sync_method_clubs(db: Session, method: ClubPaymentMethod, clubs: List[Club]) -> None:
    desired = {int(c.id) for c in clubs}
    existing = {
        int(mc.club_id): mc for mc in (method.method_clubs or [])
    }
    for club_id, row in list(existing.items()):
        if club_id not in desired:
            db.delete(row)
    for club in clubs:
        if int(club.id) not in existing:
            db.add(
                ClubPaymentMethodClub(method_id=int(method.id), club_id=int(club.id))
            )
    method.club_id = int(clubs[0].id)


def _stats_for_methods(
    db: Session, method_ids: List[int]
) -> dict[int, tuple[Decimal, int, int]]:
    if not method_ids:
        return {}
    used_only = (
        db.query(
            ManualDepositRequest.method_id,
            func.coalesce(func.sum(ManualDepositRequest.amount), 0),
        )
        .filter(ManualDepositRequest.method_id.in_(method_ids))
        .group_by(ManualDepositRequest.method_id)
        .all()
    )
    used = {int(mid): Decimal(str(total)) for mid, total in used_only if mid is not None}
    count_rows = (
        db.query(
            ManualDepositRequest.method_id,
            func.count(ManualDepositRequest.id),
        )
        .filter(ManualDepositRequest.method_id.in_(method_ids))
        .group_by(ManualDepositRequest.method_id)
        .all()
    )
    total_counts = {int(mid): int(cnt) for mid, cnt in count_rows if mid is not None}
    unchecked_rows = (
        db.query(
            ManualDepositRequest.method_id,
            func.count(ManualDepositRequest.id),
        )
        .filter(
            ManualDepositRequest.method_id.in_(method_ids),
            ManualDepositRequest.trade_record_checked.is_(False),
        )
        .group_by(ManualDepositRequest.method_id)
        .all()
    )
    unchecked = {int(mid): int(cnt) for mid, cnt in unchecked_rows if mid is not None}
    return {
        mid: (
            used.get(mid, Decimal("0")),
            unchecked.get(mid, 0),
            total_counts.get(mid, 0),
        )
        for mid in method_ids
    }


def _row_clubs_for_methods(
    db: Session, method_ids: List[int]
) -> dict[int, List[UnionMethodClubRead]]:
    if not method_ids:
        return {}
    rows = (
        db.query(
            ManualDepositRequest.method_id,
            ManualDepositRequest.club_id,
            Club.name,
        )
        .outerjoin(Club, Club.id == ManualDepositRequest.club_id)
        .filter(ManualDepositRequest.method_id.in_(method_ids))
        .distinct()
        .all()
    )
    out: dict[int, List[UnionMethodClubRead]] = {mid: [] for mid in method_ids}
    seen: dict[int, set[int]] = {mid: set() for mid in method_ids}
    for mid, club_id, club_name in rows:
        if mid is None or club_id is None:
            continue
        mid_i = int(mid)
        cid = int(club_id)
        if cid in seen.get(mid_i, set()):
            continue
        seen.setdefault(mid_i, set()).add(cid)
        out.setdefault(mid_i, []).append(
            UnionMethodClubRead(
                id=cid,
                name=club_name if club_name else f"Club {cid}",
            )
        )
    for mid in out:
        out[mid] = sorted(out[mid], key=lambda c: c.id)
    return out


def _method_type_slug(method: ClubPaymentMethod) -> str:
    raw = getattr(method, "union_type", None)
    if raw:
        try:
            return validate_union_method_type(str(raw))
        except ValueError:
            pass
    slug = union_type_from_display_name(method.name or "")
    if slug:
        return slug
    return (method.slug or "").strip().lower()


def _to_read(
    method: ClubPaymentMethod,
    used_sum: Decimal,
    unchecked_count: int,
    deposit_request_count: int,
    row_clubs: Optional[List[UnionMethodClubRead]] = None,
) -> UnionMethodRead:
    clubs = []
    for mc in sorted(method.method_clubs or [], key=lambda x: int(x.club_id)):
        if mc.club is not None:
            clubs.append(UnionMethodClubRead(id=int(mc.club.id), name=mc.club.name))
        else:
            clubs.append(UnionMethodClubRead(id=int(mc.club_id), name=f"Club {mc.club_id}"))
    method_type = _method_type_slug(method)
    account_name = getattr(method, "payment_account_name", None)
    deposit_union = getattr(method, "deposit_union", None)
    pool_pay_type = _pool_pay_type_for_method(method)
    return UnionMethodRead(
        id=int(method.id),
        type=method_type,
        pool_pay_type=pool_pay_type,  # type: ignore[arg-type]
        deposit_union=(str(deposit_union).strip() if deposit_union else None),
        internal_identifier=method.slug or "",
        identifier_suffix=_identifier_suffix_for_method(method),
        method_tag=getattr(method, "method_tag", "") or "",
        payment_account_name=(account_name.strip() if account_name else None) or None,
        is_active=bool(method.is_active),
        sort_order=int(method.sort_order or 0),
        min_amount=method.min_amount,
        max_amount=method.max_amount,
        deposit_limit=Decimal(str(method.deposit_limit or 0)),
        clubs=clubs,
        row_clubs=list(row_clubs or []),
        used_sum=used_sum,
        unchecked_count=int(unchecked_count),
        deposit_request_count=int(deposit_request_count),
    )


def _get_union_method(db: Session, method_id: int) -> ClubPaymentMethod:
    method = (
        db.query(ClubPaymentMethod)
        .options(
            joinedload(ClubPaymentMethod.method_clubs).joinedload(
                ClubPaymentMethodClub.club
            )
        )
        .filter(
            ClubPaymentMethod.id == int(method_id),
            ClubPaymentMethod.tracks_manual_requests.is_(True),
        )
        .one_or_none()
    )
    if not method:
        raise HTTPException(404, "Pool pay method not found")
    return method


@router.get("", response_model=List[UnionMethodRead])
def list_union_methods(
    is_active: Optional[bool] = Query(None),
    deposit_union: Optional[DepositUnionSlug] = Query(None),
    pool_pay_type: Optional[PoolPayTypeSlug] = Query(None),
    db: Session = Depends(get_db_dependency),
):
    q = (
        db.query(ClubPaymentMethod)
        .options(
            joinedload(ClubPaymentMethod.method_clubs).joinedload(
                ClubPaymentMethodClub.club
            )
        )
        .filter(ClubPaymentMethod.tracks_manual_requests.is_(True))
    )
    if is_active is not None:
        q = q.filter(ClubPaymentMethod.is_active.is_(bool(is_active)))
    if deposit_union is not None:
        q = q.filter(
            ClubPaymentMethod.deposit_union == validate_deposit_union(deposit_union)
        )
    if pool_pay_type is not None:
        q = q.filter(
            ClubPaymentMethod.pool_pay_type == validate_pool_pay_type(pool_pay_type)
        )
    methods = q.order_by(
        ClubPaymentMethod.union_type,
        ClubPaymentMethod.sort_order,
        ClubPaymentMethod.id,
    ).all()
    method_ids = [int(m.id) for m in methods]
    stats = _stats_for_methods(db, method_ids)
    row_clubs = _row_clubs_for_methods(db, method_ids)
    return [
        _to_read(
            m,
            stats.get(int(m.id), (Decimal("0"), 0, 0))[0],
            stats.get(int(m.id), (Decimal("0"), 0, 0))[1],
            stats.get(int(m.id), (Decimal("0"), 0, 0))[2],
            row_clubs.get(int(m.id), []),
        )
        for m in methods
    ]


@router.put("/reorder")
def reorder_union_methods(
    body: UnionMethodReorderBody,
    db: Session = Depends(get_db_dependency),
):
    method_type = validate_union_method_type(body.type)
    rows = (
        db.query(ClubPaymentMethod)
        .filter(
            ClubPaymentMethod.tracks_manual_requests.is_(True),
            ClubPaymentMethod.direction == "deposit",
            ClubPaymentMethod.union_type == method_type,
            ClubPaymentMethod.is_active.is_(True),
        )
        .all()
    )
    by_id = {int(m.id): m for m in rows}
    order_ids = [int(i) for i in body.order]
    if set(order_ids) != set(by_id.keys()):
        raise HTTPException(
            400,
            "Order must include exactly the active pool pay methods for this type.",
        )
    for idx, method_id in enumerate(order_ids):
        by_id[method_id].sort_order = idx
    db.flush()
    return {"ok": True}


@router.get("/{method_id}", response_model=UnionMethodRead)
def get_union_method(method_id: int, db: Session = Depends(get_db_dependency)):
    method = _get_union_method(db, method_id)
    stats = _stats_for_methods(db, [int(method.id)])
    used, unchecked, deposit_count = stats.get(int(method.id), (Decimal("0"), 0, 0))
    row_clubs = _row_clubs_for_methods(db, [int(method.id)]).get(int(method.id), [])
    return _to_read(method, used, unchecked, deposit_count, row_clubs)


@router.post("", response_model=UnionMethodRead, status_code=201)
def create_union_method(body: UnionMethodCreate, db: Session = Depends(get_db_dependency)):
    try:
        validated = UnionMethodCreate.model_validate(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    body = validated
    clubs = _validate_club_ids(db, body.club_ids)
    method_type = validate_union_method_type(body.type)
    pool_pay_type = validate_pool_pay_type(body.pool_pay_type)
    deposit_union = (
        validate_deposit_union(body.deposit_union)
        if pool_pay_type == "union_method"
        else None
    )
    internal_id = _build_slug_from_parts(
        method_type=method_type,
        pool_pay_type=pool_pay_type,
        identifier_suffix=body.identifier_suffix,
        db=db,
    )
    display_name = union_type_display_name(method_type)
    account_name = (body.payment_account_name or "").strip() or None
    method = ClubPaymentMethod(
        club_id=int(clubs[0].id),
        direction="deposit",
        name=display_name,
        slug=internal_id,
        union_type=method_type,
        pool_pay_type=pool_pay_type,
        deposit_union=deposit_union,
        method_tag=body.method_tag.strip(),
        payment_account_name=account_name,
        min_amount=body.min_amount,
        max_amount=body.max_amount,
        deposit_limit=body.deposit_limit,
        has_sub_options=False,
        is_active=True,
        is_public=True,
        tracks_manual_requests=True,
        manual_request_variant_name=None,
        first_time_linking_enabled=False,
        first_time_bind_mode=None,
        accumulated_amount=0,
        sort_order=_next_sort_order(db, method_type),
    )
    try:
        apply_manual_trade_request_constraints(method)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if method.min_amount is not None and method.max_amount is not None:
        if method.min_amount > method.max_amount:
            raise HTTPException(400, "Min amount cannot be greater than max amount.")
    db.add(method)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(400, "Internal identifier must be unique.") from e
    _sync_method_clubs(db, method, clubs)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(400, "Internal identifier must be unique.") from e
    method = _get_union_method(db, int(method.id))
    return _to_read(method, Decimal("0"), 0, 0, [])


@router.put("/{method_id}", response_model=UnionMethodRead)
def update_union_method(
    method_id: int,
    body: UnionMethodUpdate,
    db: Session = Depends(get_db_dependency),
):
    method = _get_union_method(db, method_id)
    data = body.model_dump(exclude_unset=True)
    club_ids = data.pop("club_ids", None)

    method_type = _method_type_slug(method)
    pool_pay_type = _pool_pay_type_for_method(method)
    type_changed = False
    pool_pay_type_changed = False

    if "pool_pay_type" in data and data["pool_pay_type"] is not None:
        pool_pay_type = validate_pool_pay_type(data.pop("pool_pay_type"))
        method.pool_pay_type = pool_pay_type
        pool_pay_type_changed = True
        if pool_pay_type == "large_cashout":
            method.deposit_union = None
    if "type" in data and data["type"] is not None:
        method_type = validate_union_method_type(data.pop("type"))
        method.union_type = method_type
        method.name = union_type_display_name(method_type)
        type_changed = True
    if "deposit_union" in data:
        raw_union = data.pop("deposit_union")
        if pool_pay_type == "large_cashout" and raw_union is not None:
            raise HTTPException(400, "deposit_union is not allowed for large cashout.")
        method.deposit_union = (
            validate_deposit_union(raw_union) if raw_union is not None else None
        )
    elif pool_pay_type == "union_method" and method.deposit_union is None:
        raise HTTPException(400, "deposit_union is required for union method pool pay.")

    rebuild_slug = type_changed or pool_pay_type_changed
    if "identifier_suffix" in data and data["identifier_suffix"] is not None:
        suffix = data.pop("identifier_suffix")
        rebuild_slug = True
    else:
        suffix = _identifier_suffix_for_method(method)

    if rebuild_slug:
        method.slug = _build_slug_from_parts(
            method_type=method_type,
            pool_pay_type=pool_pay_type,
            identifier_suffix=suffix,
            db=db,
            exclude_method_id=int(method.id),
        )
    if "method_tag" in data and data["method_tag"] is not None:
        method.method_tag = data.pop("method_tag").strip()
    if "payment_account_name" in data:
        raw = data.pop("payment_account_name")
        method.payment_account_name = (raw or "").strip() or None
    for field, value in data.items():
        setattr(method, field, value)
    try:
        apply_manual_trade_request_constraints(method)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if method.min_amount is not None and method.max_amount is not None:
        if method.min_amount > method.max_amount:
            raise HTTPException(400, "Min amount cannot be greater than max amount.")
    if club_ids is not None:
        clubs = _validate_club_ids(db, club_ids)
        _sync_method_clubs(db, method, clubs)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(400, "Internal identifier must be unique.") from e
    method = _get_union_method(db, method_id)
    stats = _stats_for_methods(db, [int(method.id)])
    used, unchecked, deposit_count = stats.get(int(method.id), (Decimal("0"), 0, 0))
    row_clubs = _row_clubs_for_methods(db, [int(method.id)]).get(int(method.id), [])
    return _to_read(method, used, unchecked, deposit_count, row_clubs)


@router.post("/{method_id}/retire", response_model=UnionMethodRead)
def retire_union_method(method_id: int, db: Session = Depends(get_db_dependency)):
    method = _get_union_method(db, method_id)
    method.is_active = False
    db.flush()
    method = _get_union_method(db, method_id)
    stats = _stats_for_methods(db, [int(method.id)])
    used, unchecked, deposit_count = stats.get(int(method.id), (Decimal("0"), 0, 0))
    row_clubs = _row_clubs_for_methods(db, [int(method.id)]).get(int(method.id), [])
    return _to_read(method, used, unchecked, deposit_count, row_clubs)


@router.delete("/{method_id}", status_code=204)
def delete_union_method(method_id: int, db: Session = Depends(get_db_dependency)):
    method = _get_union_method(db, method_id)
    method_id_i = int(method.id)
    db.query(ManualDepositRequest).filter(
        ManualDepositRequest.method_id == method_id_i
    ).delete(synchronize_session=False)
    db.query(ClubPaymentMethod).filter(
        ClubPaymentMethod.id == method_id_i,
        ClubPaymentMethod.tracks_manual_requests.is_(True),
    ).delete(synchronize_session=False)
    db.flush()


@router.post("/{method_id}/reactivate", response_model=UnionMethodRead)
def reactivate_union_method(method_id: int, db: Session = Depends(get_db_dependency)):
    method = _get_union_method(db, method_id)
    method.is_active = True
    method.is_public = True
    try:
        apply_manual_trade_request_constraints(method)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.flush()
    method = _get_union_method(db, method_id)
    stats = _stats_for_methods(db, [int(method.id)])
    used, unchecked, deposit_count = stats.get(int(method.id), (Decimal("0"), 0, 0))
    row_clubs = _row_clubs_for_methods(db, [int(method.id)]).get(int(method.id), [])
    return _to_read(method, used, unchecked, deposit_count, row_clubs)
