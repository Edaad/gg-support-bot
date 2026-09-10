"""XLSX export routes for unified payments."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.method_owner import normalize_method_owner
from api.payments_export import build_payments_workbook
from api.routes.payments import _get_club_or_404, _parse_dt, _raise_db_schema_error
from api.unified_payments import (
    UnifiedPaymentFilters,
    fetch_all_unified_rows,
    validate_unified_method_for_scope,
)
from db.connection import get_db_dependency

router = APIRouter(
    prefix="/api/payments",
    tags=["payments"],
    dependencies=[Depends(get_current_admin)],
)


def _export_filename(prefix: str, method: str, from_dt: str | None, to_dt: str | None) -> str:
    parts = [prefix, method]
    if from_dt:
        parts.append(from_dt[:10])
    if to_dt:
        parts.append(to_dt[:10])
    return f"{'-'.join(parts)}.xlsx"


def _stream_xlsx(content: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/all/export.xlsx")
def export_all_payments_xlsx(
    method: str = Query("all"),
    deposit_union: str | None = Query(None),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    q: str | None = Query(None),
    club_id: int | None = Query(None),
    db: Session = Depends(get_db_dependency),
):
    method_slug = validate_unified_method_for_scope("all", method)
    if club_id is not None:
        _get_club_or_404(db, club_id)
    filters = UnifiedPaymentFilters(
        from_dt=_parse_dt(from_dt),
        to_dt=_parse_dt(to_dt),
        q=q,
        club_id=club_id,
        deposit_union=deposit_union,
    )
    try:
        rows, _summary = fetch_all_unified_rows(
            db, scope="all", owner=None, method=method_slug, filters=filters
        )
    except HTTPException:
        raise
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)
    if not rows:
        raise HTTPException(400, "No payments to export for the selected filters.")
    content = build_payments_workbook(db, rows)
    return _stream_xlsx(
        content, _export_filename("payments-all", method_slug, from_dt, to_dt)
    )


@router.get("/owner/{owner}/export.xlsx")
def export_owner_payments_xlsx(
    owner: str,
    method: str = Query("all"),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    q: str | None = Query(None),
    club_id: int | None = Query(None),
    db: Session = Depends(get_db_dependency),
):
    owner_slug = normalize_method_owner(owner)
    method_slug = (method or "all").strip().lower()
    if method_slug != "all":
        raise HTTPException(400, "XLSX export is only supported when method=all.")
    if club_id is not None:
        _get_club_or_404(db, club_id)
    filters = UnifiedPaymentFilters(
        from_dt=_parse_dt(from_dt),
        to_dt=_parse_dt(to_dt),
        q=q,
        club_id=club_id,
    )
    try:
        rows, _summary = fetch_all_unified_rows(
            db,
            scope="owner",
            owner=owner_slug,
            method="all",
            filters=filters,
        )
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)
    if not rows:
        raise HTTPException(400, "No payments to export for the selected filters.")
    content = build_payments_workbook(db, rows)
    return _stream_xlsx(
        content,
        _export_filename(f"payments-{owner_slug}", "all", from_dt, to_dt),
    )


@router.get("/union/export.xlsx")
def export_union_payments_xlsx(
    method: str = Query("all"),
    deposit_union: str | None = Query(None),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    q: str | None = Query(None),
    club_id: int | None = Query(None),
    db: Session = Depends(get_db_dependency),
):
    method_slug = validate_unified_method_for_scope("union", method)
    if method_slug != "all":
        raise HTTPException(400, "XLSX export is only supported when method=all.")
    if club_id is not None:
        _get_club_or_404(db, club_id)
    filters = UnifiedPaymentFilters(
        from_dt=_parse_dt(from_dt),
        to_dt=_parse_dt(to_dt),
        q=q,
        club_id=club_id,
        deposit_union=deposit_union,
    )
    try:
        rows, _summary = fetch_all_unified_rows(
            db, scope="union", owner=None, method="all", filters=filters
        )
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)
    if not rows:
        raise HTTPException(400, "No payments to export for the selected filters.")
    content = build_payments_workbook(db, rows)
    return _stream_xlsx(
        content, _export_filename("union-payments", "all", from_dt, to_dt)
    )
