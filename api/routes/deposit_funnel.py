"""Deposit funnel analytics API — /deposit through chips credited."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.payments_helpers import apply_analytics_chat_exclusion, cents_to_usd
from api.schemas_payments import (
    DepositFunnelEventListResponse,
    DepositFunnelEventRead,
    DepositFunnelStepCount,
    DepositFunnelSummaryResponse,
)
from bot.services.deposit_funnel_events import FUNNEL_STEP_ORDER
from db.connection import get_db_dependency
from db.models import Club, DepositFunnelEvent

router = APIRouter(
    prefix="/api/deposits/funnel",
    tags=["deposit-funnel"],
    dependencies=[Depends(get_current_admin)],
)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

_MIGRATION_HINT = (
    "Run: python migrate_deposit_funnel_events.py (or heroku run … on the web dyno)"
)

_STEP_LABELS: dict[str, str] = {
    "deposit_started": "/deposit started",
    "referral_completed": "Referral answered",
    "amount_entered": "Amount entered",
    "union_chosen": "Union chosen",
    "method_chosen": "Method chosen",
    "bind_setup_completed": "Bind setup completed",
    "instructions_sent": "Instructions sent",
    "payment_received": "Payment received",
    "payment_bound": "Payment bound",
    "chips_credited": "Chips credited",
    "chips_confirmed": "Chips confirmed",
}


def _parse_dt(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        if len(raw) == 10:
            return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), _MAX_LIMIT))


def _raise_db_schema_error(exc: ProgrammingError) -> None:
    msg = str(exc.orig) if exc.orig else str(exc)
    if "deposit_funnel_events" in msg and "does not exist" in msg:
        raise HTTPException(503, f"Deposit funnel analytics table is missing. {_MIGRATION_HINT}")


def _funnel_events_query(
    db: Session,
    *,
    club_id: int | None,
    method_slug: str | None,
    from_dt: datetime | None,
    to_dt: datetime | None,
    is_first_deposit: bool | None,
    requires_method_setup: bool | None,
    step: str | None,
    exclude_test_chats: bool,
):
    q = db.query(DepositFunnelEvent)
    if club_id is not None:
        q = q.filter(DepositFunnelEvent.club_id == club_id)
    if from_dt is not None:
        q = q.filter(DepositFunnelEvent.created_at >= from_dt)
    if to_dt is not None:
        q = q.filter(DepositFunnelEvent.created_at <= to_dt)
    if is_first_deposit is not None:
        q = q.filter(DepositFunnelEvent.is_first_deposit == is_first_deposit)
    if requires_method_setup is not None:
        q = q.filter(
            DepositFunnelEvent.requires_method_setup == requires_method_setup
        )
    if step and step.strip():
        q = q.filter(DepositFunnelEvent.step == step.strip())
    if exclude_test_chats:
        q = apply_analytics_chat_exclusion(
            db, q, DepositFunnelEvent.telegram_chat_id
        )
    slug = (method_slug or "").strip().lower()
    if slug:
        matching_sessions = (
            db.query(DepositFunnelEvent.deposit_session_id)
            .filter(DepositFunnelEvent.method_slug == slug)
        )
        if club_id is not None:
            matching_sessions = matching_sessions.filter(
                DepositFunnelEvent.club_id == club_id
            )
        if from_dt is not None:
            matching_sessions = matching_sessions.filter(
                DepositFunnelEvent.created_at >= from_dt
            )
        if to_dt is not None:
            matching_sessions = matching_sessions.filter(
                DepositFunnelEvent.created_at <= to_dt
            )
        if exclude_test_chats:
            matching_sessions = apply_analytics_chat_exclusion(
                db, matching_sessions, DepositFunnelEvent.telegram_chat_id
            )
        q = q.filter(
            DepositFunnelEvent.deposit_session_id.in_(matching_sessions.distinct())
        )
    return q


def _event_read(row: DepositFunnelEvent, club_name: str | None) -> DepositFunnelEventRead:
    amount_cents = int(row.amount_cents) if row.amount_cents is not None else None
    return DepositFunnelEventRead(
        id=int(row.id),
        deposit_session_id=str(row.deposit_session_id),
        step=str(row.step),
        club_id=int(row.club_id) if row.club_id is not None else None,
        club_name=club_name,
        telegram_user_id=(
            int(row.telegram_user_id) if row.telegram_user_id is not None else None
        ),
        telegram_chat_id=int(row.telegram_chat_id),
        method_slug=str(row.method_slug) if row.method_slug else None,
        amount_cents=amount_cents,
        amount_usd=cents_to_usd(amount_cents) if amount_cents is not None else None,
        is_first_deposit=bool(row.is_first_deposit),
        requires_method_setup=bool(row.requires_method_setup),
        metadata=row.metadata_json,
        created_at=row.created_at,
    )


@router.get("/summary", response_model=DepositFunnelSummaryResponse)
def deposit_funnel_summary(
    club_id: int | None = Query(None),
    method: str | None = Query(None, description="payment method slug filter"),
    is_first_deposit: bool | None = Query(None),
    requires_method_setup: bool | None = Query(None),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    exclude_test_chats: bool = Query(True),
    db: Session = Depends(get_db_dependency),
):
    dt_from = _parse_dt(from_dt)
    dt_to = _parse_dt(to_dt)
    try:
        base_q = _funnel_events_query(
            db,
            club_id=club_id,
            method_slug=method,
            from_dt=dt_from,
            to_dt=dt_to,
            is_first_deposit=is_first_deposit,
            requires_method_setup=requires_method_setup,
            step=None,
            exclude_test_chats=exclude_test_chats,
        )
        counts_by_step = {
            str(row[0]): int(row[1] or 0)
            for row in base_q.with_entities(
                DepositFunnelEvent.step,
                func.count(func.distinct(DepositFunnelEvent.deposit_session_id)),
            )
            .group_by(DepositFunnelEvent.step)
            .all()
        }
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)
        raise

    started = counts_by_step.get("deposit_started", 0)
    steps: list[DepositFunnelStepCount] = []
    for step in FUNNEL_STEP_ORDER:
        count = counts_by_step.get(step, 0)
        conversion = (count / started) if started else None
        steps.append(
            DepositFunnelStepCount(
                step=step,
                label=_STEP_LABELS.get(step, step),
                count=count,
                conversion_rate=conversion,
            )
        )
    return DepositFunnelSummaryResponse(
        club_id=club_id,
        started=started,
        steps=steps,
    )


@router.get("/events", response_model=DepositFunnelEventListResponse)
def list_deposit_funnel_events(
    club_id: int | None = Query(None),
    method: str | None = Query(None),
    step: str | None = Query(None),
    is_first_deposit: bool | None = Query(None),
    requires_method_setup: bool | None = Query(None),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    limit: int = Query(_DEFAULT_LIMIT),
    offset: int = Query(0, ge=0),
    exclude_test_chats: bool = Query(True),
    db: Session = Depends(get_db_dependency),
):
    dt_from = _parse_dt(from_dt)
    dt_to = _parse_dt(to_dt)
    limit = _clamp_limit(limit)
    try:
        q = _funnel_events_query(
            db,
            club_id=club_id,
            method_slug=method,
            from_dt=dt_from,
            to_dt=dt_to,
            is_first_deposit=is_first_deposit,
            requires_method_setup=requires_method_setup,
            step=step,
            exclude_test_chats=exclude_test_chats,
        )
        total = q.count()
        rows = (
            q.order_by(DepositFunnelEvent.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)
        raise

    club_names: dict[int, str | None] = {}
    items: list[DepositFunnelEventRead] = []
    for row in rows:
        cid = int(row.club_id) if row.club_id is not None else None
        if cid is not None and cid not in club_names:
            club = db.query(Club).filter(Club.id == cid).first()
            club_names[cid] = club.name if club else None
        items.append(_event_read(row, club_names.get(cid) if cid else None))
    return DepositFunnelEventListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )
