"""Union methods API: shared multi-club manual deposit configs."""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from api.auth import get_current_admin
from api.payment_v2_helpers import apply_manual_trade_request_constraints
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


class UnionMethodClubRead(BaseModel):
    id: int
    name: str


class UnionMethodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    is_active: bool
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    deposit_limit: Decimal
    manual_request_message: str
    manual_request_variant_name: str
    clubs: List[UnionMethodClubRead]
    row_clubs: List[UnionMethodClubRead] = Field(default_factory=list)
    used_sum: Decimal
    unchecked_count: int


class UnionMethodCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    slug: str = Field(..., min_length=1, max_length=50)
    club_ids: List[int] = Field(..., min_length=1)
    deposit_limit: Decimal
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    manual_request_message: str = Field(..., min_length=1)
    manual_request_variant_name: str = Field(..., min_length=1)


class UnionMethodUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    slug: Optional[str] = Field(None, min_length=1, max_length=50)
    club_ids: Optional[List[int]] = Field(None, min_length=1)
    deposit_limit: Optional[Decimal] = None
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    manual_request_message: Optional[str] = None
    manual_request_variant_name: Optional[str] = None


def _normalize_slug(slug: str) -> str:
    return slug.strip().lower()


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
    # Legacy anchor: first selected club (sorted by id).
    method.club_id = int(clubs[0].id)


def _stats_for_methods(
    db: Session, method_ids: List[int]
) -> dict[int, tuple[Decimal, int]]:
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
        mid: (used.get(mid, Decimal("0")), unchecked.get(mid, 0)) for mid in method_ids
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


def _to_read(
    method: ClubPaymentMethod,
    used_sum: Decimal,
    unchecked_count: int,
    row_clubs: Optional[List[UnionMethodClubRead]] = None,
) -> UnionMethodRead:
    clubs = []
    for mc in sorted(method.method_clubs or [], key=lambda x: int(x.club_id)):
        if mc.club is not None:
            clubs.append(UnionMethodClubRead(id=int(mc.club.id), name=mc.club.name))
        else:
            clubs.append(UnionMethodClubRead(id=int(mc.club_id), name=f"Club {mc.club_id}"))
    return UnionMethodRead(
        id=int(method.id),
        name=method.name,
        slug=method.slug,
        is_active=bool(method.is_active),
        min_amount=method.min_amount,
        max_amount=method.max_amount,
        deposit_limit=Decimal(str(method.deposit_limit or 0)),
        manual_request_message=method.manual_request_message or "",
        manual_request_variant_name=method.manual_request_variant_name or "",
        clubs=clubs,
        row_clubs=list(row_clubs or []),
        used_sum=used_sum,
        unchecked_count=int(unchecked_count),
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
    methods = q.order_by(ClubPaymentMethod.name, ClubPaymentMethod.id).all()
    method_ids = [int(m.id) for m in methods]
    stats = _stats_for_methods(db, method_ids)
    row_clubs = _row_clubs_for_methods(db, method_ids)
    return [
        _to_read(
            m,
            stats.get(int(m.id), (Decimal("0"), 0))[0],
            stats.get(int(m.id), (Decimal("0"), 0))[1],
            row_clubs.get(int(m.id), []),
        )
        for m in methods
    ]


@router.get("/{method_id}", response_model=UnionMethodRead)
def get_union_method(method_id: int, db: Session = Depends(get_db_dependency)):
    method = _get_union_method(db, method_id)
    stats = _stats_for_methods(db, [int(method.id)])
    used, unchecked = stats.get(int(method.id), (Decimal("0"), 0))
    row_clubs = _row_clubs_for_methods(db, [int(method.id)]).get(int(method.id), [])
    return _to_read(method, used, unchecked, row_clubs)


@router.post("", response_model=UnionMethodRead, status_code=201)
def create_union_method(body: UnionMethodCreate, db: Session = Depends(get_db_dependency)):
    clubs = _validate_club_ids(db, body.club_ids)
    slug = _normalize_slug(body.slug)
    method = ClubPaymentMethod(
        club_id=int(clubs[0].id),
        direction="deposit",
        name=body.name.strip(),
        slug=slug,
        min_amount=body.min_amount,
        max_amount=body.max_amount,
        deposit_limit=body.deposit_limit,
        has_sub_options=False,
        is_active=True,
        is_public=False,
        tracks_manual_requests=True,
        manual_request_message=body.manual_request_message,
        manual_request_variant_name=body.manual_request_variant_name,
        first_time_linking_enabled=False,
        first_time_bind_mode=None,
        accumulated_amount=0,
        sort_order=0,
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
        raise HTTPException(400, "Union method slug must be unique.") from e
    _sync_method_clubs(db, method, clubs)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(400, "Union method slug must be unique.") from e
    method = _get_union_method(db, int(method.id))
    return _to_read(method, Decimal("0"), 0, [])


@router.put("/{method_id}", response_model=UnionMethodRead)
def update_union_method(
    method_id: int,
    body: UnionMethodUpdate,
    db: Session = Depends(get_db_dependency),
):
    method = _get_union_method(db, method_id)
    data = body.model_dump(exclude_unset=True)
    club_ids = data.pop("club_ids", None)
    if "slug" in data and data["slug"] is not None:
        data["slug"] = _normalize_slug(data["slug"])
    if "name" in data and data["name"] is not None:
        data["name"] = data["name"].strip()
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
        raise HTTPException(400, "Union method slug must be unique.") from e
    method = _get_union_method(db, method_id)
    stats = _stats_for_methods(db, [int(method.id)])
    used, unchecked = stats.get(int(method.id), (Decimal("0"), 0))
    row_clubs = _row_clubs_for_methods(db, [int(method.id)]).get(int(method.id), [])
    return _to_read(method, used, unchecked, row_clubs)


@router.post("/{method_id}/retire", response_model=UnionMethodRead)
def retire_union_method(method_id: int, db: Session = Depends(get_db_dependency)):
    method = _get_union_method(db, method_id)
    method.is_active = False
    db.flush()
    method = _get_union_method(db, method_id)
    stats = _stats_for_methods(db, [int(method.id)])
    used, unchecked = stats.get(int(method.id), (Decimal("0"), 0))
    row_clubs = _row_clubs_for_methods(db, [int(method.id)]).get(int(method.id), [])
    return _to_read(method, used, unchecked, row_clubs)


@router.post("/{method_id}/reactivate", response_model=UnionMethodRead)
def reactivate_union_method(method_id: int, db: Session = Depends(get_db_dependency)):
    method = _get_union_method(db, method_id)
    method.is_active = True
    method.is_public = False
    db.flush()
    method = _get_union_method(db, method_id)
    stats = _stats_for_methods(db, [int(method.id)])
    used, unchecked = stats.get(int(method.id), (Decimal("0"), 0))
    row_clubs = _row_clubs_for_methods(db, [int(method.id)]).get(int(method.id), [])
    return _to_read(method, used, unchecked, row_clubs)
