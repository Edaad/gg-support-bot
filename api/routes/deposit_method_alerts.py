"""Admin CRUD for deposit method weekly threshold alerts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth import require_admin
from bot.services.deposit_method_alerts import (
    ALERT_METHODS,
    CONDITION_REGISTRY,
    condition_summaries,
    conditions_equal,
    conditions_from_api,
    distinct_variants_for_method,
    eastern_week_bounds_utc,
    evaluate_deposit_method_alerts,
    normalize_method,
    normalize_variant,
    week_stats_map,
)
from db.connection import get_db_dependency
from db.models import DepositMethodAlert

router = APIRouter(
    prefix="/api/deposit-alerts",
    tags=["deposit-alerts"],
    dependencies=[Depends(require_admin)],
)

AlertMethod = Literal["venmo", "zelle", "cashapp", "paypal", "crypto"]


class ConditionIn(BaseModel):
    type: str
    operator: str = "gte"
    threshold: Optional[float | int] = None
    threshold_usd: Optional[float] = None


class DepositAlertCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    method: AlertMethod
    variant: str = Field(..., min_length=1, max_length=255)
    is_active: bool = True
    conditions: list[ConditionIn]


class DepositAlertUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    method: Optional[AlertMethod] = None
    variant: Optional[str] = Field(None, min_length=1, max_length=255)
    is_active: Optional[bool] = None
    conditions: Optional[list[ConditionIn]] = None


class DepositAlertRead(BaseModel):
    id: int
    name: str
    method: str
    variant: str
    is_active: bool
    conditions: list[dict[str, Any]]
    last_fired_week_id: Optional[str] = None
    last_fired_at: Optional[datetime] = None
    week_id: str
    week_volume_cents: int
    week_volume_usd: Decimal
    week_tx_count: int
    alerted_this_week: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DepositAlertVariantList(BaseModel):
    items: list[str]


class ConditionTypeInfo(BaseModel):
    type: str
    label: str


def _http_value_error(exc: ValueError) -> HTTPException:
    return HTTPException(400, str(exc))


def _to_read(
    row: DepositMethodAlert,
    *,
    week_id: str,
    volume_cents: int,
    tx_count: int,
) -> DepositAlertRead:
    return DepositAlertRead(
        id=row.id,
        name=row.name,
        method=row.method,
        variant=row.variant,
        is_active=bool(row.is_active),
        conditions=condition_summaries(list(row.conditions or [])),
        last_fired_week_id=row.last_fired_week_id,
        last_fired_at=row.last_fired_at,
        week_id=week_id,
        week_volume_cents=volume_cents,
        week_volume_usd=(Decimal(volume_cents) / 100).quantize(Decimal("0.01")),
        week_tx_count=tx_count,
        alerted_this_week=row.last_fired_week_id == week_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _parse_conditions(raw: list[ConditionIn] | None) -> list[dict]:
    try:
        return conditions_from_api(
            [c.model_dump(exclude_none=True) for c in (raw or [])]
        )
    except ValueError as exc:
        raise _http_value_error(exc) from exc


@router.get("/condition-types", response_model=list[ConditionTypeInfo])
def list_condition_types():
    return [
        ConditionTypeInfo(type=spec.type, label=spec.label)
        for spec in CONDITION_REGISTRY.values()
    ]


@router.get("/variants", response_model=DepositAlertVariantList)
def list_variants(
    method: str,
    db: Session = Depends(get_db_dependency),
):
    try:
        method_slug = normalize_method(method)
    except ValueError as exc:
        raise _http_value_error(exc) from exc
    return DepositAlertVariantList(items=distinct_variants_for_method(db, method_slug))


@router.get("", response_model=list[DepositAlertRead])
def list_alerts(db: Session = Depends(get_db_dependency)):
    week = eastern_week_bounds_utc()
    rows = db.query(DepositMethodAlert).order_by(DepositMethodAlert.id.desc()).all()
    pairs = [(r.method, r.variant) for r in rows]
    stats = week_stats_map(db, pairs, week=week)
    return [
        _to_read(
            row,
            week_id=week.week_id,
            volume_cents=stats[(row.method, row.variant)].volume_cents,
            tx_count=stats[(row.method, row.variant)].tx_count,
        )
        for row in rows
    ]


def _read_with_stats(db: Session, row: DepositMethodAlert) -> DepositAlertRead:
    week = eastern_week_bounds_utc()
    stats = week_stats_map(db, [(row.method, row.variant)], week=week)[
        (row.method, row.variant)
    ]
    return _to_read(
        row,
        week_id=week.week_id,
        volume_cents=stats.volume_cents,
        tx_count=stats.tx_count,
    )


def _set_method_or_variant(
    row: DepositMethodAlert,
    *,
    method: str | None,
    variant: str | None,
) -> bool:
    changed = False
    if method is not None:
        try:
            new_method = normalize_method(method)
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        if new_method != row.method:
            row.method = new_method
            changed = True
    if variant is not None:
        try:
            new_variant = normalize_variant(variant)
        except ValueError as exc:
            raise _http_value_error(exc) from exc
        if new_variant != row.variant:
            row.variant = new_variant
            changed = True
    return changed


def _apply_alert_update(row: DepositMethodAlert, body: DepositAlertUpdate) -> bool:
    """Apply PATCH fields. Returns whether evaluate should run."""
    prev_active = bool(row.is_active)
    clear_fired = _set_method_or_variant(row, method=body.method, variant=body.variant)

    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "name is required")
        row.name = name

    if body.conditions is not None:
        conditions = _parse_conditions(body.conditions)
        if not conditions_equal(list(row.conditions or []), conditions):
            row.conditions = conditions
            clear_fired = True

    if body.is_active is not None:
        row.is_active = bool(body.is_active)

    if clear_fired:
        row.last_fired_week_id = None
        row.last_fired_at = None

    becoming_active = bool(row.is_active) and not prev_active
    return bool(row.is_active) and (becoming_active or clear_fired)


@router.post("", response_model=DepositAlertRead)
async def create_alert(
    body: DepositAlertCreate,
    db: Session = Depends(get_db_dependency),
):
    try:
        method = normalize_method(body.method)
        variant = normalize_variant(body.variant)
    except ValueError as exc:
        raise _http_value_error(exc) from exc
    if method not in ALERT_METHODS:
        raise HTTPException(400, "Invalid method")

    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "name is required")

    conditions = _parse_conditions(body.conditions)
    row = DepositMethodAlert(
        name=name,
        method=method,
        variant=variant,
        is_active=bool(body.is_active),
        conditions=conditions,
    )
    db.add(row)
    db.flush()

    if row.is_active:
        await evaluate_deposit_method_alerts(db, method=method, variant=variant)

    db.refresh(row)
    return _read_with_stats(db, row)


@router.patch("/{alert_id}", response_model=DepositAlertRead)
async def update_alert(
    alert_id: int,
    body: DepositAlertUpdate,
    db: Session = Depends(get_db_dependency),
):
    row = db.get(DepositMethodAlert, alert_id)
    if row is None:
        raise HTTPException(404, "Alert not found")

    should_evaluate = _apply_alert_update(row, body)
    db.flush()

    if should_evaluate:
        await evaluate_deposit_method_alerts(db, method=row.method, variant=row.variant)

    db.refresh(row)
    return _read_with_stats(db, row)


@router.delete("/{alert_id}", status_code=204)
def delete_alert(alert_id: int, db: Session = Depends(get_db_dependency)):
    row = db.get(DepositMethodAlert, alert_id)
    if row is None:
        raise HTTPException(404, "Alert not found")
    db.delete(row)
