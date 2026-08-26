"""Manual trade-request deposit queue API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, joinedload

from api.auth import get_current_admin
from db.connection import get_db_dependency
from db.models import ClubPaymentMethod, ManualDepositRequest

router = APIRouter(
    prefix="/api",
    tags=["manual-deposit-requests"],
    dependencies=[Depends(get_current_admin)],
)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


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
    amount: Decimal
    trade_record_checked: bool
    created_at: datetime
    club: Optional[ManualDepositRequestClubRead] = None


class ManualDepositRequestListResponse(BaseModel):
    items: List[ManualDepositRequestRead]
    total: int
    limit: int
    offset: int


class ManualDepositRequestUpdate(BaseModel):
    trade_record_checked: bool


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
        amount=Decimal(str(row.amount)),
        trade_record_checked=bool(row.trade_record_checked),
        created_at=row.created_at,
        club=club,
    )


def _list_query(
    db: Session,
    *,
    club_id: Optional[int] = None,
    method_id: Optional[int] = None,
    method_slug: Optional[str] = None,
    trade_record_checked: Optional[bool] = None,
    include_inactive_methods: bool = True,
):
    q = db.query(ManualDepositRequest).options(
        joinedload(ManualDepositRequest.club),
    )
    if club_id is not None:
        q = q.filter(ManualDepositRequest.club_id == int(club_id))
    if method_id is not None:
        q = q.filter(ManualDepositRequest.method_id == int(method_id))
    if method_slug:
        q = q.filter(ManualDepositRequest.method_slug == method_slug.strip().lower())
    if trade_record_checked is not None:
        q = q.filter(
            ManualDepositRequest.trade_record_checked.is_(bool(trade_record_checked))
        )
    if not include_inactive_methods:
        q = q.join(
            ClubPaymentMethod,
            ClubPaymentMethod.id == ManualDepositRequest.method_id,
            isouter=True,
        ).filter(
            (ManualDepositRequest.method_id.is_(None))
            | (ClubPaymentMethod.is_active.is_(True))
        )
    return q.order_by(ManualDepositRequest.created_at.desc(), ManualDepositRequest.id.desc())


@router.get(
    "/manual-deposit-requests",
    response_model=ManualDepositRequestListResponse,
)
def list_manual_deposit_requests(
    club_id: Optional[int] = None,
    method_id: Optional[int] = None,
    method_slug: Optional[str] = None,
    trade_record_checked: Optional[bool] = None,
    include_inactive_methods: bool = Query(True),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db_dependency),
):
    q = _list_query(
        db,
        club_id=club_id,
        method_id=method_id,
        method_slug=method_slug,
        trade_record_checked=trade_record_checked,
        include_inactive_methods=include_inactive_methods,
    )
    total = q.count()
    rows = q.offset(offset).limit(limit).all()
    return ManualDepositRequestListResponse(
        items=[_to_read(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/v2/methods/{method_id}/manual-deposit-requests",
    response_model=ManualDepositRequestListResponse,
)
def list_method_manual_deposit_requests(
    method_id: int,
    trade_record_checked: Optional[bool] = None,
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db_dependency),
):
    method = db.query(ClubPaymentMethod).get(int(method_id))
    if not method:
        raise HTTPException(404, "Method not found")
    q = _list_query(
        db,
        method_id=int(method_id),
        trade_record_checked=trade_record_checked,
        include_inactive_methods=True,
    )
    total = q.count()
    rows = q.offset(offset).limit(limit).all()
    return ManualDepositRequestListResponse(
        items=[_to_read(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch(
    "/manual-deposit-requests/{request_id}",
    response_model=ManualDepositRequestRead,
)
def update_manual_deposit_request(
    request_id: int,
    body: ManualDepositRequestUpdate,
    db: Session = Depends(get_db_dependency),
):
    row = (
        db.query(ManualDepositRequest)
        .options(joinedload(ManualDepositRequest.club))
        .filter(ManualDepositRequest.id == int(request_id))
        .first()
    )
    if not row:
        raise HTTPException(404, "Request not found")
    row.trade_record_checked = bool(body.trade_record_checked)
    db.flush()
    db.refresh(row)
    return _to_read(row)


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
