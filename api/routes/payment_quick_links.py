"""CRUD for Payments-page quick-access links."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from api.auth import get_current_admin, require_admin
from api.gto_club import lookup_gto_club_id
from api.payment_quick_links import (
    create_quick_link,
    delete_quick_link,
    list_quick_links,
    update_quick_link,
)
from api.schemas import (
    PaymentQuickLinkCreate,
    PaymentQuickLinkListResponse,
    PaymentQuickLinkRead,
    PaymentQuickLinkUpdate,
)
from db.connection import get_db_dependency

router = APIRouter(
    prefix="/api/payments/quick-links",
    tags=["payments"],
    dependencies=[Depends(get_current_admin)],
)

_MIGRATION_HINT = (
    "Run: alembic upgrade head (the Heroku release phase does this on deploy)"
)


def _schema_error() -> HTTPException:
    return HTTPException(
        503,
        f"Payment quick links table is missing. {_MIGRATION_HINT}",
    )


@router.get("", response_model=PaymentQuickLinkListResponse)
def get_quick_links(
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    try:
        gto_club_id = lookup_gto_club_id(db)
        links = list_quick_links(db, role=role, gto_club_id=gto_club_id)
    except ProgrammingError as exc:
        raise _schema_error() from exc
    return PaymentQuickLinkListResponse(
        links=[PaymentQuickLinkRead.model_validate(row) for row in links]
    )


@router.post("", response_model=PaymentQuickLinkRead, status_code=201)
def post_quick_link(
    body: PaymentQuickLinkCreate,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db_dependency),
):
    try:
        data = create_quick_link(
            db,
            title=body.title,
            url=body.url,
            method=body.method,
            club_id=body.club_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ProgrammingError as exc:
        raise _schema_error() from exc
    return PaymentQuickLinkRead.model_validate(data)


@router.patch("/{link_id}", response_model=PaymentQuickLinkRead)
def patch_quick_link(
    link_id: int,
    body: PaymentQuickLinkUpdate,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db_dependency),
):
    updates = body.model_dump(exclude_unset=True)
    try:
        data = update_quick_link(db, link_id, **updates)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ProgrammingError as exc:
        raise _schema_error() from exc
    if not data:
        raise HTTPException(404, "Link not found")
    return PaymentQuickLinkRead.model_validate(data)


@router.delete("/{link_id}", status_code=204)
def remove_quick_link(
    link_id: int,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db_dependency),
):
    try:
        ok = delete_quick_link(db, link_id)
    except ProgrammingError as exc:
        raise _schema_error() from exc
    if not ok:
        raise HTTPException(404, "Link not found")
