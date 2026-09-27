"""Club bank logins via Plaid Link. Tokens stay on the server."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.auth import ROLE_ADMIN, ROLE_GTO, get_current_admin
from api.gto_club import assert_gto_club_id
from api.plaid_bank import (
    SLOT_CAP,
    ZELLE_PAGE_SIZE,
    PlaidConfigError,
    PlaidError,
    PlaidItemView,
    club_sync_state,
    create_link_token,
    exchange_public_token,
    list_items,
    list_zelle,
    require_plaid_config,
    sync_club_transactions,
)
from db.connection import get_db_dependency
from db.models import Club

router = APIRouter(prefix="/api/clubs", tags=["plaid"])


class PlaidLinkTokenRequest(BaseModel):
    item_id: Optional[int] = None


class PlaidExchangeRequest(BaseModel):
    public_token: str
    institution_id: Optional[str] = None
    institution_name: Optional[str] = None


class PlaidItemRead(BaseModel):
    id: int
    institution_name: str
    institution_id: Optional[str] = None
    status: str
    error_code: Optional[str] = None
    created_at: datetime


class PlaidItemListResponse(BaseModel):
    slots_used: int
    slot_cap: int
    items: list[PlaidItemRead]


class PlaidLinkTokenResponse(BaseModel):
    link_token: str


class PlaidZelleRead(BaseModel):
    id: int
    institution_name: str
    txn_date: date
    amount: float
    name: Optional[str] = None
    payer: Optional[str] = None
    payee: Optional[str] = None
    memo: Optional[str] = None
    original_description: Optional[str] = None
    payment_method: Optional[str] = None
    payment_channel: Optional[str] = None
    pending: bool
    detail: Optional[dict] = None


class PlaidZelleListResponse(BaseModel):
    total: int
    offset: int
    limit: int
    syncing: bool
    sync_error: Optional[str] = None
    items: list[PlaidZelleRead]


class PlaidZelleSyncResponse(BaseModel):
    syncing: bool


def _require_config() -> None:
    try:
        require_plaid_config()
    except PlaidConfigError as exc:
        raise HTTPException(503, str(exc)) from exc


def _guard(role: str, club_id: int, db: Session) -> None:
    if role not in (ROLE_ADMIN, ROLE_GTO):
        raise HTTPException(403, "Club access denied")
    assert_gto_club_id(role, club_id, db)
    found = db.query(Club.id).filter(Club.id == club_id).first()
    if found is None:
        raise HTTPException(404, "Club not found")


def _read(view: PlaidItemView) -> PlaidItemRead:
    return PlaidItemRead(
        id=view.id,
        institution_name=view.institution_name,
        institution_id=view.institution_id,
        status=view.status,
        error_code=view.error_code,
        created_at=view.created_at,
    )


def _plaid_http(exc: PlaidError) -> HTTPException:
    status = 502 if exc.error_code == "UNAVAILABLE" else 400
    return HTTPException(status, exc.error_message)


@router.get("/{club_id}/plaid/items", response_model=PlaidItemListResponse)
def get_plaid_items(
    club_id: int,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    _guard(role, club_id, db)
    _require_config()
    try:
        slots_used, views = list_items(db, club_id)
    except PlaidConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    return PlaidItemListResponse(
        slots_used=slots_used,
        slot_cap=SLOT_CAP,
        items=[_read(view) for view in views],
    )


@router.post("/{club_id}/plaid/link-token", response_model=PlaidLinkTokenResponse)
def post_plaid_link_token(
    club_id: int,
    body: Optional[PlaidLinkTokenRequest] = None,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    _guard(role, club_id, db)
    _require_config()
    item_row_id = body.item_id if body is not None else None
    try:
        link_token = create_link_token(db, club_id, item_row_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PlaidConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    except PlaidError as exc:
        raise _plaid_http(exc) from exc
    return PlaidLinkTokenResponse(link_token=link_token)


@router.post("/{club_id}/plaid/items", response_model=PlaidItemRead)
def post_plaid_item(
    club_id: int,
    body: PlaidExchangeRequest,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    _guard(role, club_id, db)
    _require_config()
    try:
        row = exchange_public_token(
            db,
            club_id,
            body.public_token,
            body.institution_id,
            body.institution_name,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PlaidConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    except PlaidError as exc:
        raise _plaid_http(exc) from exc
    return PlaidItemRead(
        id=row.id,
        institution_name=row.institution_name,
        institution_id=row.institution_id,
        status="connected",
        error_code=None,
        created_at=row.created_at,
    )


def _zelle_read(row, institution_name: str) -> PlaidZelleRead:
    detail = None
    if row.detail_json:
        try:
            parsed = json.loads(row.detail_json)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            detail = parsed
    return PlaidZelleRead(
        id=row.id,
        institution_name=institution_name,
        txn_date=row.txn_date,
        amount=float(row.amount),
        name=row.name,
        payer=row.payer,
        payee=row.payee,
        memo=row.memo,
        original_description=row.original_description,
        payment_method=row.payment_method,
        payment_channel=row.payment_channel,
        pending=bool(row.pending),
        detail=detail,
    )


@router.get("/{club_id}/plaid/zelle", response_model=PlaidZelleListResponse)
def get_plaid_zelle(
    club_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(ZELLE_PAGE_SIZE, ge=1, le=ZELLE_PAGE_SIZE),
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    _guard(role, club_id, db)
    _require_config()
    total, rows = list_zelle(db, club_id, offset, limit)
    syncing, sync_error = club_sync_state(db, club_id)
    return PlaidZelleListResponse(
        total=total,
        offset=offset,
        limit=limit,
        syncing=syncing,
        sync_error=sync_error,
        items=[_zelle_read(row, name) for row, name in rows],
    )


@router.post(
    "/{club_id}/plaid/zelle/sync",
    response_model=PlaidZelleSyncResponse,
    status_code=202,
)
def post_plaid_zelle_sync(
    club_id: int,
    background_tasks: BackgroundTasks,
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    _guard(role, club_id, db)
    _require_config()
    background_tasks.add_task(sync_club_transactions, club_id)
    return PlaidZelleSyncResponse(syncing=True)
