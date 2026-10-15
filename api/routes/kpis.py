"""Club KPIs API: the six daily/weekly KPIs for one period vs the one before it."""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.auth import ROLE_ADMIN, ROLE_GTO, get_current_admin
from api.gto_club import resolve_gto_list_club_id
from api.kpis import compute_kpis
from api.schemas_kpis import KpiResponse
from db.connection import get_db_dependency

router = APIRouter(
    prefix="/api/kpis",
    tags=["kpis"],
    dependencies=[Depends(get_current_admin)],
)


def _parse_day(value: Optional[str], name: str) -> Optional[date]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(400, f"{name} must be YYYY-MM-DD") from exc


@router.get("", response_model=KpiResponse)
def get_kpis(
    granularity: str = Query("week", description="day | week | month | custom"),
    date_: Optional[str] = Query(
        None,
        alias="date",
        description="Any America/New_York day inside the period (day/week/month)",
    ),
    from_: Optional[str] = Query(None, alias="from", description="Custom start day"),
    to: Optional[str] = Query(None, description="Custom end day (inclusive)"),
    club_id: Optional[int] = Query(None),
    role: str = Depends(get_current_admin),
    db: Session = Depends(get_db_dependency),
):
    if role not in (ROLE_ADMIN, ROLE_GTO):
        raise HTTPException(403, "KPIs are limited to admins")
    effective_club_id, empty = resolve_gto_list_club_id(role, club_id, db)
    if empty:
        raise HTTPException(404, "ClubGTO not found")
    try:
        return compute_kpis(
            db,
            granularity=granularity,
            anchor=_parse_day(date_, "date"),
            start=_parse_day(from_, "from"),
            end=_parse_day(to, "to"),
            club_id=effective_club_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
