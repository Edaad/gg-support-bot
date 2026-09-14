"""Union-scoped unified payments (TR-checked manual deposits)."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.gto_club import resolve_gto_list_club_id
from api.routes.payments import _clamp_limit, _get_club_or_404, _parse_dt, _raise_db_schema_error
from api.schemas_payments import OwnerPaymentSummary, UnifiedPaymentListResponse
from api.unified_payments import (
    UnifiedPaymentFilters,
    fetch_unified_page,
    validate_unified_method_for_scope,
)
from db.connection import get_db_dependency

router = APIRouter(
    prefix="/api/payments/union",
    tags=["payments"],
    dependencies=[Depends(get_current_admin)],
)

_DEFAULT_LIMIT = 50


@router.get("/payments", response_model=UnifiedPaymentListResponse)
def list_union_unified_payments(
    method: str = Query("all"),
    deposit_union: str | None = Query(None),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    q: str | None = Query(None),
    club_id: int | None = Query(None),
    limit: int = Query(_DEFAULT_LIMIT),
    offset: int = Query(0),
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    method_slug = validate_unified_method_for_scope("union", method)
    limit = _clamp_limit(limit)
    offset = max(0, offset)
    parsed_from = _parse_dt(from_dt)
    parsed_to = _parse_dt(to_dt)
    club_id, empty = resolve_gto_list_club_id(role, club_id, db)
    if empty:
        return UnifiedPaymentListResponse(
            scope="union",
            method=method_slug,
            items=[],
            total=0,
            limit=limit,
            offset=offset,
            summary=OwnerPaymentSummary(
                total_count=0,
                total_amount_cents=0,
                total_amount_usd=Decimal("0"),
            ),
        )
    if club_id is not None:
        _get_club_or_404(db, club_id)

    filters = UnifiedPaymentFilters(
        from_dt=parsed_from,
        to_dt=parsed_to,
        q=q,
        club_id=club_id,
        deposit_union=deposit_union,
    )
    try:
        items, total, summary = fetch_unified_page(
            db,
            scope="union",
            owner=None,
            method=method_slug,
            filters=filters,
            limit=limit,
            offset=offset,
        )
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)

    return UnifiedPaymentListResponse(
        scope="union",
        method=method_slug,
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        summary=summary,
    )
