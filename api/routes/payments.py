"""Payments dashboard API — club-scoped Stripe customers and checkout sessions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.payments_helpers import (
    apply_customer_search,
    apply_session_filters,
    cents_to_usd,
    customer_session_counts,
    list_stripe_deposit_methods,
    resolve_group_title,
    resolve_method_display,
    stripe_dashboard_payment_url,
    stripe_dashboard_session_url,
)
from api.schemas_payments import (
    PaymentProviderRead,
    StripeCheckoutSessionListResponse,
    StripeCheckoutSessionRead,
    StripeCustomerListResponse,
    StripeCustomerRead,
    StripeMethodOptionRead,
)
from db.connection import get_db_dependency
from db.models import Club, StripeCheckoutSession, StripeCustomer

router = APIRouter(
    prefix="/api/payments",
    tags=["payments"],
    dependencies=[Depends(get_current_admin)],
)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


def _parse_dt(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as e:
        raise HTTPException(400, f"Invalid datetime: {value}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, _MAX_LIMIT))


def _get_club_or_404(db: Session, club_id: int) -> Club:
    club = db.query(Club).filter(Club.id == club_id).first()
    if not club:
        raise HTTPException(404, "Club not found")
    return club


@router.get("/providers", response_model=List[PaymentProviderRead])
def list_providers():
    return [PaymentProviderRead(id="stripe", label="Stripe")]


@router.get("/stripe/methods", response_model=List[StripeMethodOptionRead])
def list_stripe_methods(
    club_id: int = Query(...),
    db: Session = Depends(get_db_dependency),
):
    _get_club_or_404(db, club_id)
    return [StripeMethodOptionRead.model_validate(row) for row in list_stripe_deposit_methods(db, club_id)]


@router.get("/stripe/customers", response_model=StripeCustomerListResponse)
def list_stripe_customers(
    club_id: int = Query(...),
    q: str | None = Query(None),
    limit: int = Query(_DEFAULT_LIMIT),
    offset: int = Query(0),
    db: Session = Depends(get_db_dependency),
):
    _get_club_or_404(db, club_id)
    limit = _clamp_limit(limit)
    offset = max(0, offset)

    base = db.query(StripeCustomer).filter(StripeCustomer.club_id == club_id)
    base = apply_customer_search(base, q)
    total = base.count()
    rows = (
        base.order_by(StripeCustomer.updated_at.desc(), StripeCustomer.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    counts = customer_session_counts(db, club_id)

    items: list[StripeCustomerRead] = []
    for row in rows:
        title, gg_id, player_name = resolve_group_title(
            db,
            row.telegram_chat_id,
            fallback_gg_player_id=row.gg_player_id,
            fallback_player_display_name=row.player_display_name,
        )
        items.append(
            StripeCustomerRead(
                id=row.id,
                telegram_chat_id=row.telegram_chat_id,
                club_id=row.club_id,
                stripe_customer_id=row.stripe_customer_id,
                gg_player_id=gg_id,
                player_display_name=player_name,
                group_title=title,
                session_count=counts.get(row.stripe_customer_id, 0),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )

    return StripeCustomerListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/stripe/sessions", response_model=StripeCheckoutSessionListResponse)
def list_stripe_sessions(
    club_id: int = Query(...),
    status: str | None = Query(None, description="open | complete | expired | all"),
    method_id: int | None = Query(None),
    manual_only: bool = Query(False, description="Sessions from /stripe (no payment_method_id)"),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    limit: int = Query(_DEFAULT_LIMIT),
    offset: int = Query(0),
    db: Session = Depends(get_db_dependency),
):
    _get_club_or_404(db, club_id)
    limit = _clamp_limit(limit)
    offset = max(0, offset)

    base = db.query(StripeCheckoutSession)
    base = apply_session_filters(
        base,
        club_id=club_id,
        status=status,
        method_id=method_id,
        manual_only=manual_only,
        from_dt=_parse_dt(from_dt),
        to_dt=_parse_dt(to_dt),
    )
    total = base.count()
    rows = (
        base.order_by(StripeCheckoutSession.created_at.desc(), StripeCheckoutSession.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    customer_by_stripe_id: dict[str, StripeCustomer] = {}
    if rows:
        customer_ids = {row.stripe_customer_id for row in rows}
        for cust in (
            db.query(StripeCustomer)
            .filter(
                StripeCustomer.club_id == club_id,
                StripeCustomer.stripe_customer_id.in_(customer_ids),
            )
            .all()
        ):
            customer_by_stripe_id[cust.stripe_customer_id] = cust

    items: list[StripeCheckoutSessionRead] = []
    for row in rows:
        cust = customer_by_stripe_id.get(row.stripe_customer_id)
        title, gg_id, player_name = resolve_group_title(
            db,
            row.telegram_chat_id,
            fallback_gg_player_id=cust.gg_player_id if cust else None,
            fallback_player_display_name=cust.player_display_name if cust else None,
        )
        method_name, method_slug = resolve_method_display(db, club_id, row.payment_method_id)
        items.append(
            StripeCheckoutSessionRead(
                id=row.id,
                stripe_checkout_session_id=row.stripe_checkout_session_id,
                stripe_customer_id=row.stripe_customer_id,
                telegram_chat_id=row.telegram_chat_id,
                club_id=row.club_id,
                amount_cents=row.amount_cents,
                amount_usd=cents_to_usd(row.amount_cents),
                currency=row.currency,
                status=row.status,
                payment_method_id=row.payment_method_id,
                method_name=method_name,
                method_slug=method_slug,
                stripe_payment_intent_id=row.stripe_payment_intent_id,
                group_title=title,
                gg_player_id=gg_id,
                player_display_name=player_name,
                stripe_dashboard_url=stripe_dashboard_session_url(row.stripe_checkout_session_id),
                stripe_payment_url=stripe_dashboard_payment_url(row.stripe_payment_intent_id),
                created_at=row.created_at,
                completed_at=row.completed_at,
                updated_at=row.updated_at,
            )
        )

    return StripeCheckoutSessionListResponse(items=items, total=total, limit=limit, offset=offset)
