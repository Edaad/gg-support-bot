"""Club bank logins via Plaid Link. Tokens stay on the server."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.auth import ROLE_ADMIN, ROLE_GTO, get_current_admin
from api.gto_club import assert_gto_club_id
from api.plaid_bank import (
    SLOT_CAP,
    PlaidConfigError,
    PlaidError,
    PlaidItemView,
    create_link_token,
    exchange_public_token,
    list_items,
    require_plaid_config,
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
