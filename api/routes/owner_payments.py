"""Owner-scoped payments dashboard API (RT / Vaughn / Mateos)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.method_owner import normalize_method_owner
from api.payments_helpers import (
    OWNER_INGEST_METHODS,
    OWNER_METHODS_BY_OWNER,
    aggregate_owner_payment_query,
    apply_owner_ingest_filters,
    apply_owner_stripe_filters,
    build_cashapp_payment_read,
    build_crypto_payment_read,
    build_paypal_payment_read,
    build_venmo_payment_read,
    build_zelle_payment_read,
    cents_to_usd,
    distinct_owner_ingest_variants,
    distinct_owner_stripe_variants,
    lookup_gg_nickname,
    resolve_group_title,
    resolve_method_display,
    stripe_dashboard_payment_url,
    stripe_dashboard_session_url,
)
from api.routes.payments import _clamp_limit, _parse_dt, _raise_db_schema_error
from api.schemas_payments import (
    CashAppPaymentRead,
    CryptoPaymentRead,
    OwnerPaymentListResponse,
    OwnerPaymentSummary,
    OwnerVariantListResponse,
    OwnerVariantOptionRead,
    PayPalPaymentRead,
    StripeCheckoutSessionRead,
    VenmoPaymentRead,
    ZellePaymentRead,
)
from db.connection import get_db_dependency
from db.models import StripeCheckoutSession, StripeCustomer

router = APIRouter(
    prefix="/api/payments/owner",
    tags=["payments"],
    dependencies=[Depends(get_current_admin)],
)

_DEFAULT_LIMIT = 50

_BUILD_READ_BY_METHOD = {
    "venmo": build_venmo_payment_read,
    "zelle": build_zelle_payment_read,
    "cashapp": build_cashapp_payment_read,
    "paypal": build_paypal_payment_read,
    "crypto": build_crypto_payment_read,
}

_READ_MODEL_BY_METHOD = {
    "venmo": VenmoPaymentRead,
    "zelle": ZellePaymentRead,
    "cashapp": CashAppPaymentRead,
    "paypal": PayPalPaymentRead,
    "crypto": CryptoPaymentRead,
}


def _validate_owner_method(owner: str, method: str) -> tuple[str, str]:
    owner_slug = normalize_method_owner(owner)
    method_slug = (method or "").strip().lower()
    allowed = OWNER_METHODS_BY_OWNER.get(owner_slug)
    if allowed is None or method_slug not in allowed:
        allowed_labels = ", ".join(sorted(allowed or ()))
        raise HTTPException(
            400,
            f"Method '{method_slug}' is not available for owner '{owner_slug}'. "
            f"Allowed: {allowed_labels}",
        )
    return owner_slug, method_slug


def _build_stripe_session_read(db: Session, row: StripeCheckoutSession) -> StripeCheckoutSessionRead:
    club_id = int(row.club_id)
    cust = (
        db.query(StripeCustomer)
        .filter(
            StripeCustomer.club_id == club_id,
            StripeCustomer.stripe_customer_id == row.stripe_customer_id,
        )
        .first()
    )
    title, gg_id = resolve_group_title(
        db,
        row.telegram_chat_id,
        fallback_gg_player_id=cust.gg_player_id if cust else None,
    )
    method_name, method_slug = resolve_method_display(db, club_id, row.payment_method_id)
    return StripeCheckoutSessionRead(
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
        gg_nickname=lookup_gg_nickname(db, club_id, gg_id),
        stripe_dashboard_url=stripe_dashboard_session_url(row.stripe_checkout_session_id),
        stripe_payment_url=stripe_dashboard_payment_url(row.stripe_payment_intent_id),
        created_at=row.created_at,
        completed_at=row.completed_at,
        updated_at=row.updated_at,
    )


@router.get("/{owner}/payments", response_model=OwnerPaymentListResponse)
def list_owner_payments(
    owner: str,
    method: str = Query(...),
    variant: str | None = Query(None),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    q: str | None = Query(None),
    limit: int = Query(_DEFAULT_LIMIT),
    offset: int = Query(0),
    db: Session = Depends(get_db_dependency),
):
    owner_slug, method_slug = _validate_owner_method(owner, method)
    limit = _clamp_limit(limit)
    offset = max(0, offset)
    parsed_from = _parse_dt(from_dt)
    parsed_to = _parse_dt(to_dt)

    try:
        if method_slug == "stripe":
            if owner_slug != "round-table":
                raise HTTPException(400, "Stripe is only available for owner round-table.")
            base = db.query(StripeCheckoutSession)
            base = apply_owner_stripe_filters(
                base,
                variant=variant,
                from_dt=parsed_from,
                to_dt=parsed_to,
                q=q,
            )
            total_count, total_amount_cents = aggregate_owner_payment_query(
                base, StripeCheckoutSession.amount_cents
            )
            rows = (
                base.order_by(
                    StripeCheckoutSession.created_at.desc(),
                    StripeCheckoutSession.id.desc(),
                )
                .offset(offset)
                .limit(limit)
                .all()
            )
            items = [_build_stripe_session_read(db, row) for row in rows]
        else:
            payment_cls = OWNER_INGEST_METHODS[method_slug]
            base = db.query(payment_cls)
            base = apply_owner_ingest_filters(
                base,
                payment_cls,
                method_owner=owner_slug,
                variant=variant,
                from_dt=parsed_from,
                to_dt=parsed_to,
                q=q,
            )
            total_count, total_amount_cents = aggregate_owner_payment_query(
                base, payment_cls.amount_cents
            )
            rows = (
                base.order_by(payment_cls.created_at.desc(), payment_cls.id.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            build_read = _BUILD_READ_BY_METHOD[method_slug]
            read_model = _READ_MODEL_BY_METHOD[method_slug]
            items = [read_model.model_validate(build_read(db, row)) for row in rows]
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)

    return OwnerPaymentListResponse(
        method=method_slug,
        items=items,
        total=total_count,
        limit=limit,
        offset=offset,
        summary=OwnerPaymentSummary(
            total_count=total_count,
            total_amount_cents=total_amount_cents,
            total_amount_usd=cents_to_usd(total_amount_cents),
        ),
    )


@router.get("/{owner}/variants", response_model=OwnerVariantListResponse)
def list_owner_variants(
    owner: str,
    method: str = Query(...),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    db: Session = Depends(get_db_dependency),
):
    owner_slug, method_slug = _validate_owner_method(owner, method)
    parsed_from = _parse_dt(from_dt)
    parsed_to = _parse_dt(to_dt)

    try:
        if method_slug == "stripe":
            if owner_slug != "round-table":
                raise HTTPException(400, "Stripe is only available for owner round-table.")
            stripe_variants = distinct_owner_stripe_variants(
                db, from_dt=parsed_from, to_dt=parsed_to
            )
            items = [
                OwnerVariantOptionRead(value=row["id"], label=row["label"])
                for row in stripe_variants
            ]
        else:
            payment_cls = OWNER_INGEST_METHODS[method_slug]
            values = distinct_owner_ingest_variants(
                db,
                payment_cls,
                method_owner=owner_slug,
                from_dt=parsed_from,
                to_dt=parsed_to,
            )
            items = [OwnerVariantOptionRead(value=v, label=v) for v in values]
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)

    return OwnerVariantListResponse(items=items)
