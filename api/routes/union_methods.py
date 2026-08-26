"""Union methods API: shared multi-club manual deposit configs."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from api.auth import get_current_admin
from api.payment_v2_helpers import apply_manual_trade_request_constraints
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
    prefix="/api/union-methods",
    tags=["union-methods"],
    dependencies=[Depends(get_current_admin)],
)

UnionMethodTypeSlug = Literal["zelle", "cashapp", "applepay"]


class UnionMethodClubRead(BaseModel):
    id: int
    name: str


class UnionMethodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    method: str
    name: str
    tag: str
    is_active: bool
    sort_order: int
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    deposit_limit: Decimal
    manual_request_message: str
    clubs: List[UnionMethodClubRead]
    row_clubs: List[UnionMethodClubRead] = Field(default_factory=list)
    used_sum: Decimal
    unchecked_count: int
    deposit_request_count: int


class UnionMethodCreate(BaseModel):
    method: UnionMethodTypeSlug
    tag: str = Field(..., min_length=1, max_length=50)
    club_ids: List[int] = Field(..., min_length=1)
    deposit_limit: Decimal
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    manual_request_message: str = Field(..., min_length=1)


class UnionMethodUpdate(BaseModel):
    method: Optional[UnionMethodTypeSlug] = None
    tag: Optional[str] = Field(None, min_length=1, max_length=50)
    club_ids: Optional[List[int]] = Field(None, min_length=1)
    deposit_limit: Optional[Decimal] = None
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    manual_request_message: Optional[str] = None


class UnionMethodReorderBody(BaseModel):
    method: UnionMethodTypeSlug
    order: List[int] = Field(..., min_length=1)


def _normalize_tag(tag: str) -> str:
    return tag.strip().lower()


def _union_tag_exists(db: Session, tag: str, *, exclude_method_id: Optional[int] = None) -> bool:
    q = db.query(ClubPaymentMethod.id).filter(
        ClubPaymentMethod.tracks_manual_requests.is_(True),
        ClubPaymentMethod.direction == "deposit",
        ClubPaymentMethod.slug == tag,
    )
    if exclude_method_id is not None:
        q = q.filter(ClubPaymentMethod.id != int(exclude_method_id))
    return q.first() is not None


def _ensure_unique_tag(
    db: Session,
    base: str,
    *,
    exclude_method_id: Optional[int] = None,
) -> str:
    normalized = _normalize_tag(base)
    if not normalized:
        raise HTTPException(400, "Tag is required.")
    if not _union_tag_exists(db, normalized, exclude_method_id=exclude_method_id):
        return normalized
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    candidate = f"{normalized}-{stamp}"
    if not _union_tag_exists(db, candidate, exclude_method_id=exclude_method_id):
        return candidate
    raise HTTPException(400, "Could not allocate a unique tag; try a different tag.")


def _next_sort_order(db: Session, method_type: str) -> int:
    display = union_type_display_name(method_type)
    max_order = (
        db.query(func.coalesce(func.max(ClubPaymentMethod.sort_order), -1))
        .filter(
            ClubPaymentMethod.tracks_manual_requests.is_(True),
            ClubPaymentMethod.direction == "deposit",
            ClubPaymentMethod.name == display,
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
    return UnionMethodRead(
        id=int(method.id),
        method=method_type,
        name=method.name,
        tag=method.slug,
        is_active=bool(method.is_active),
        sort_order=int(method.sort_order or 0),
        min_amount=method.min_amount,
        max_amount=method.max_amount,
        deposit_limit=Decimal(str(method.deposit_limit or 0)),
        manual_request_message=method.manual_request_message or "",
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
        raise HTTPException(404, "Union method not found")
    return method


@router.get("", response_model=List[UnionMethodRead])
def list_union_methods(
    is_active: Optional[bool] = Query(None),
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
    methods = q.order_by(
        ClubPaymentMethod.name,
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
    method_type = validate_union_method_type(body.method)
    display = union_type_display_name(method_type)
    rows = (
        db.query(ClubPaymentMethod)
        .filter(
            ClubPaymentMethod.tracks_manual_requests.is_(True),
            ClubPaymentMethod.direction == "deposit",
            ClubPaymentMethod.name == display,
            ClubPaymentMethod.is_active.is_(True),
        )
        .all()
    )
    by_id = {int(m.id): m for m in rows}
    order_ids = [int(i) for i in body.order]
    if set(order_ids) != set(by_id.keys()):
        raise HTTPException(
            400,
            "Order must include exactly the active union methods for this type.",
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
    clubs = _validate_club_ids(db, body.club_ids)
    method_type = validate_union_method_type(body.method)
    tag = _ensure_unique_tag(db, body.tag)
    display_name = union_type_display_name(method_type)
    method = ClubPaymentMethod(
        club_id=int(clubs[0].id),
        direction="deposit",
        name=display_name,
        slug=tag,
        min_amount=body.min_amount,
        max_amount=body.max_amount,
        deposit_limit=body.deposit_limit,
        has_sub_options=False,
        is_active=True,
        is_public=True,
        tracks_manual_requests=True,
        manual_request_message=body.manual_request_message,
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
        raise HTTPException(400, "Union method tag must be unique.") from e
    _sync_method_clubs(db, method, clubs)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(400, "Union method tag must be unique.") from e
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
    if "method" in data and data["method"] is not None:
        method_type = validate_union_method_type(data.pop("method"))
        method.name = union_type_display_name(method_type)
    if "tag" in data and data["tag"] is not None:
        method.slug = _ensure_unique_tag(
            db, data.pop("tag"), exclude_method_id=int(method.id)
        )
    if "manual_request_message" in data and data["manual_request_message"] is not None:
        method.manual_request_message = data.pop("manual_request_message")
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
        raise HTTPException(400, "Union method tag must be unique.") from e
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
