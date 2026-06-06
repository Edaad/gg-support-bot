"""Payments dashboard API — club-scoped Stripe customers and checkout sessions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.payments_helpers import (
    apply_analytics_chat_exclusion,
    apply_customer_search,
    apply_session_filters,
    apply_venmo_payment_filters,
    apply_zelle_payment_filters,
    build_venmo_payment_read,
    build_zelle_payment_read,
    cents_to_usd,
    compute_zelle_payment_summary,
    customer_total_deposited_cents,
    list_stripe_deposit_methods,
    list_venmo_payer_aggregates,
    list_zelle_payer_aggregates,
    lookup_gg_nickname,
    resolve_group_title,
    resolve_method_display,
    stripe_dashboard_payment_url,
    stripe_dashboard_session_url,
)
from api.schemas_payments import (
    BindAttemptListResponse,
    BindAttemptRead,
    BindKindCount,
    BindingAttemptFunnel,
    BindingSummaryResponse,
    BindingViaCount,
    GroupBindingListResponse,
    GroupBindingRead,
    PaymentProviderRead,
    UnbindResponse,
    StripeCheckoutSessionListResponse,
    StripeCheckoutSessionRead,
    StripeCustomerListResponse,
    StripeCustomerRead,
    StripeMethodOptionRead,
    VenmoBindRequest,
    VenmoBindResponse,
    VenmoPayerListResponse,
    VenmoPayerRead,
    VenmoPaymentListResponse,
    VenmoPaymentRead,
    ZelleBindRequest,
    ZelleBindResponse,
    ZellePayerListResponse,
    ZellePayerRead,
    ZellePaymentListResponse,
    ZellePaymentRead,
    ZellePaymentSummaryByClub,
    ZellePaymentSummaryResponse,
)
from bot.services.venmo_payments import bind_venmo_payment_by_id
from bot.services.zelle_payments import bind_zelle_payment_by_id
from db.connection import get_db_dependency
from bot.services.payment_method_binding import unbind_by_id
from db.models import (
    Club,
    ClubPaymentTierVariant,
    GroupPaymentMethodBinding,
    PaymentMethodBindAttempt,
    StripeCheckoutSession,
    StripeCustomer,
    VenmoPayment,
    ZellePayment,
)

router = APIRouter(
    prefix="/api/payments",
    tags=["payments"],
    dependencies=[Depends(get_current_admin)],
)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

_MIGRATION_HINT = (
    "Run: python migrate_stripe_deposit_tracking.py && "
    "python migrate_stripe_checkout_session_lifecycle.py (or heroku run … on the web dyno)"
)
_VENMO_MIGRATION_HINT = "Run: python migrate_venmo_payments.py (or heroku run … on the web dyno)"
_ZELLE_MIGRATION_HINT = "Run: python migrate_zelle_payments.py (or heroku run … on the web dyno)"
_BINDINGS_MIGRATION_HINT = (
    "Run: python migrate_payment_method_bindings.py (or heroku run … on the web dyno)"
)

BOUND_VIA_FILTER_ALIASES: dict[str, tuple[str, ...]] = {
    "manual": ("manual_notification", "manual_dashboard"),
}

_FIRST_TIME_BOUND_VIA = frozenset({"special_amount", "memo_emoji"})


def _resolve_bound_via_filter(bound_via: str | None) -> tuple[str, ...] | None:
    """Return concrete bound_via values for a filter param, or None for no filter."""
    raw = (bound_via or "").strip().lower()
    if not raw or raw == "all":
        return None
    if raw in BOUND_VIA_FILTER_ALIASES:
        return BOUND_VIA_FILTER_ALIASES[raw]
    return (raw,)


def _apply_bound_via_filter(q, column, bound_via: str | None):
    values = _resolve_bound_via_filter(bound_via)
    if values is None:
        return q
    if len(values) == 1:
        return q.filter(column == values[0])
    return q.filter(column.in_(values))


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


def _raise_db_schema_error(exc: ProgrammingError) -> None:
    msg = str(exc.orig) if exc.orig else str(exc)
    low = msg.lower()
    if "stripe_checkout_sessions" in low or "stripe_customers" in low:
        if "does not exist" in low or "undefinedcolumn" in low.replace(" ", ""):
            raise HTTPException(
                503,
                f"Stripe payments tables or columns are missing. {_MIGRATION_HINT}",
            ) from exc
    if "venmo_payments" in low or "venmo_payer_bindings" in low:
        if "does not exist" in low or "undefinedcolumn" in low.replace(" ", ""):
            raise HTTPException(
                503,
                f"Venmo payments tables or columns are missing. {_VENMO_MIGRATION_HINT}",
            ) from exc
    if "zelle_payments" in low or "zelle_payer_bindings" in low:
        if "does not exist" in low or "undefinedcolumn" in low.replace(" ", ""):
            raise HTTPException(
                503,
                f"Zelle payments tables or columns are missing. {_ZELLE_MIGRATION_HINT}",
            ) from exc
    if "group_payment_method_bindings" in low or "payment_method_bind_attempts" in low:
        if "does not exist" in low or "undefinedcolumn" in low.replace(" ", ""):
            raise HTTPException(
                503,
                f"Payment method binding tables are missing. {_BINDINGS_MIGRATION_HINT}",
            ) from exc
    raise HTTPException(503, f"Database schema error: {msg}") from exc


@router.get("/providers", response_model=List[PaymentProviderRead])
def list_providers():
    return [
        PaymentProviderRead(id="stripe", label="Stripe"),
        PaymentProviderRead(id="venmo", label="Venmo"),
        PaymentProviderRead(id="zelle", label="Zelle"),
    ]


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

    try:
        base = db.query(StripeCustomer).filter(StripeCustomer.club_id == club_id)
        base = apply_customer_search(base, q)
        total = base.count()
        rows = (
            base.order_by(StripeCustomer.updated_at.desc(), StripeCustomer.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        totals = customer_total_deposited_cents(db, club_id)
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)

    items: list[StripeCustomerRead] = []
    for row in rows:
        title, gg_id = resolve_group_title(
            db,
            row.telegram_chat_id,
            fallback_gg_player_id=row.gg_player_id,
        )
        deposited_cents = totals.get(row.stripe_customer_id, 0)
        items.append(
            StripeCustomerRead(
                id=row.id,
                telegram_chat_id=row.telegram_chat_id,
                club_id=row.club_id,
                gg_player_id=gg_id,
                gg_nickname=lookup_gg_nickname(db, club_id, gg_id),
                group_title=title,
                total_deposited_cents=deposited_cents,
                total_deposited_usd=cents_to_usd(deposited_cents),
                created_at=row.created_at,
            )
        )

    return StripeCustomerListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/stripe/sessions", response_model=StripeCheckoutSessionListResponse)
def list_stripe_sessions(
    club_id: int = Query(...),
    status: str | None = Query(
        "complete",
        description="Completed payments only (open/unpaid are not stored).",
    ),
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

    try:
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
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)

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
        title, gg_id = resolve_group_title(
            db,
            row.telegram_chat_id,
            fallback_gg_player_id=cust.gg_player_id if cust else None,
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
                gg_nickname=lookup_gg_nickname(db, club_id, gg_id),
                stripe_dashboard_url=stripe_dashboard_session_url(row.stripe_checkout_session_id),
                stripe_payment_url=stripe_dashboard_payment_url(row.stripe_payment_intent_id),
                created_at=row.created_at,
                completed_at=row.completed_at,
                updated_at=row.updated_at,
            )
        )

    return StripeCheckoutSessionListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/venmo/payments", response_model=VenmoPaymentListResponse)
def list_venmo_payments(
    club_id: int = Query(...),
    status: str = Query("all", description="bound | unbound | all"),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    q: str | None = Query(None),
    include_test: bool = Query(False),
    limit: int = Query(_DEFAULT_LIMIT),
    offset: int = Query(0),
    db: Session = Depends(get_db_dependency),
):
    _get_club_or_404(db, club_id)
    limit = _clamp_limit(limit)
    offset = max(0, offset)

    try:
        base = db.query(VenmoPayment)
        base = apply_venmo_payment_filters(
            base,
            club_id=club_id,
            status=status,
            from_dt=_parse_dt(from_dt),
            to_dt=_parse_dt(to_dt),
            include_test=include_test,
            q=q,
        )
        total = base.count()
        rows = (
            base.order_by(VenmoPayment.created_at.desc(), VenmoPayment.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)

    items = [VenmoPaymentRead.model_validate(build_venmo_payment_read(db, row)) for row in rows]
    return VenmoPaymentListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/venmo/payers", response_model=VenmoPayerListResponse)
def list_venmo_payers(
    club_id: int = Query(...),
    q: str | None = Query(None),
    limit: int = Query(_DEFAULT_LIMIT),
    offset: int = Query(0),
    db: Session = Depends(get_db_dependency),
):
    _get_club_or_404(db, club_id)
    limit = _clamp_limit(limit)
    offset = max(0, offset)

    try:
        agg = list_venmo_payer_aggregates(db, club_id, q)
        subq = agg.subquery()
        total = db.query(func.count()).select_from(subq).scalar() or 0
        rows = agg.offset(offset).limit(limit).all()
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)

    items: list[VenmoPayerRead] = []
    for row in rows:
        chat_id = int(row.telegram_chat_id) if row.telegram_chat_id else None
        title, gg_id = resolve_group_title(db, chat_id) if chat_id else (None, None)
        total_cents = int(row.total_cents or 0)
        items.append(
            VenmoPayerRead(
                payer_name=row.payer_name,
                venmo_handle=row.venmo_handle,
                group_title=title,
                gg_player_id=gg_id,
                gg_nickname=lookup_gg_nickname(db, club_id, gg_id),
                total_deposited_cents=total_cents,
                total_deposited_usd=cents_to_usd(total_cents),
                payment_count=int(row.payment_count or 0),
                last_payment_at=row.last_payment_at,
            )
        )

    return VenmoPayerListResponse(items=items, total=int(total), limit=limit, offset=offset)


@router.post("/venmo/payments/{payment_id}/bind", response_model=VenmoBindResponse)
async def bind_venmo_payment(
    payment_id: int,
    body: VenmoBindRequest,
    db: Session = Depends(get_db_dependency),
):
    group_title = (body.group_title or "").strip()
    if not group_title:
        return VenmoBindResponse(ok=False, error="Group title is required.")

    result = await bind_venmo_payment_by_id(
        payment_id=payment_id,
        group_title_input=group_title,
    )
    if not result.ok or result.bound_group is None:
        return VenmoBindResponse(ok=False, error=result.error or "Could not bind payment.")

    group = result.bound_group
    payment = db.query(VenmoPayment).filter(VenmoPayment.id == payment_id).first()
    payment_read = None
    if payment is not None:
        payment_read = VenmoPaymentRead.model_validate(build_venmo_payment_read(db, payment))

    return VenmoBindResponse(
        ok=True,
        group_title=group.group_title,
        telegram_chat_id=group.telegram_chat_id,
        club_id=group.club_id,
        payment=payment_read,
    )


@router.get("/zelle/payments", response_model=ZellePaymentListResponse)
def list_zelle_payments(
    club_id: int = Query(...),
    status: str = Query("all", description="bound | unbound | all"),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    q: str | None = Query(None),
    include_test: bool = Query(False),
    limit: int = Query(_DEFAULT_LIMIT),
    offset: int = Query(0),
    db: Session = Depends(get_db_dependency),
):
    _get_club_or_404(db, club_id)
    limit = _clamp_limit(limit)
    offset = max(0, offset)

    try:
        base = db.query(ZellePayment)
        base = apply_zelle_payment_filters(
            base,
            club_id=club_id,
            status=status,
            from_dt=_parse_dt(from_dt),
            to_dt=_parse_dt(to_dt),
            include_test=include_test,
            q=q,
        )
        total = base.count()
        rows = (
            base.order_by(ZellePayment.created_at.desc(), ZellePayment.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)

    items = [ZellePaymentRead.model_validate(build_zelle_payment_read(db, row)) for row in rows]
    return ZellePaymentListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/zelle/payers", response_model=ZellePayerListResponse)
def list_zelle_payers(
    club_id: int = Query(...),
    q: str | None = Query(None),
    limit: int = Query(_DEFAULT_LIMIT),
    offset: int = Query(0),
    db: Session = Depends(get_db_dependency),
):
    _get_club_or_404(db, club_id)
    limit = _clamp_limit(limit)
    offset = max(0, offset)

    try:
        agg = list_zelle_payer_aggregates(db, club_id, q)
        subq = agg.subquery()
        total = db.query(func.count()).select_from(subq).scalar() or 0
        rows = agg.offset(offset).limit(limit).all()
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)

    items: list[ZellePayerRead] = []
    for row in rows:
        chat_id = int(row.telegram_chat_id) if row.telegram_chat_id else None
        title, gg_id = resolve_group_title(db, chat_id) if chat_id else (None, None)
        total_cents = int(row.total_cents or 0)
        items.append(
            ZellePayerRead(
                payer_name=row.payer_name,
                zelle_recipient=row.zelle_recipient,
                group_title=title,
                gg_player_id=gg_id,
                gg_nickname=lookup_gg_nickname(db, club_id, gg_id),
                total_deposited_cents=total_cents,
                total_deposited_usd=cents_to_usd(total_cents),
                payment_count=int(row.payment_count or 0),
                last_payment_at=row.last_payment_at,
            )
        )

    return ZellePayerListResponse(items=items, total=int(total), limit=limit, offset=offset)


@router.get("/zelle/summary", response_model=ZellePaymentSummaryResponse)
def zelle_payment_summary(
    club_id: int | None = Query(None),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    include_test: bool = Query(False),
    exclude_test_chats: bool = Query(False),
    db: Session = Depends(get_db_dependency),
):
    if club_id is not None:
        _get_club_or_404(db, club_id)

    try:
        raw = compute_zelle_payment_summary(
            db,
            club_id=club_id,
            from_dt=_parse_dt(from_dt),
            to_dt=_parse_dt(to_dt),
            include_test=include_test,
            exclude_test_chats=exclude_test_chats,
        )
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)
        raise

    club_names: dict[int, str] = {}
    by_club: list[ZellePaymentSummaryByClub] = []
    for row in raw["by_club"]:
        cid = row["club_id"]
        cname: str | None = None
        if cid is not None:
            if cid not in club_names:
                club = db.query(Club).filter(Club.id == cid).first()
                club_names[cid] = club.name if club else None
            cname = club_names.get(cid)
        amount_cents = int(row["amount_cents"])
        by_club.append(
            ZellePaymentSummaryByClub(
                club_id=cid,
                club_name=cname,
                count=int(row["count"]),
                amount_cents=amount_cents,
                amount_usd=cents_to_usd(amount_cents),
            )
        )

    total_cents = int(raw["total_amount_cents"])
    return ZellePaymentSummaryResponse(
        club_id=club_id,
        total_payments=int(raw["total_payments"]),
        bound_count=int(raw["bound_count"]),
        unbound_count=int(raw["unbound_count"]),
        auto_bound_count=int(raw["auto_bound_count"]),
        total_amount_cents=total_cents,
        total_amount_usd=cents_to_usd(total_cents),
        by_club=by_club,
    )


@router.post("/zelle/payments/{payment_id}/bind", response_model=ZelleBindResponse)
async def bind_zelle_payment(
    payment_id: int,
    body: ZelleBindRequest,
    db: Session = Depends(get_db_dependency),
):
    group_title = (body.group_title or "").strip()
    if not group_title:
        return ZelleBindResponse(ok=False, error="Group title is required.")

    result = await bind_zelle_payment_by_id(
        payment_id=payment_id,
        group_title_input=group_title,
    )
    if not result.ok or result.bound_group is None:
        return ZelleBindResponse(ok=False, error=result.error or "Could not bind payment.")

    group = result.bound_group
    payment = db.query(ZellePayment).filter(ZellePayment.id == payment_id).first()
    payment_read = None
    if payment is not None:
        payment_read = ZellePaymentRead.model_validate(build_zelle_payment_read(db, payment))

    return ZelleBindResponse(
        ok=True,
        group_title=group.group_title,
        telegram_chat_id=group.telegram_chat_id,
        club_id=group.club_id,
        payment=payment_read,
    )


@router.get("/bindings", response_model=GroupBindingListResponse)
def list_group_bindings(
    method: str = Query("venmo", description="payment_method_slug"),
    club_id: int | None = Query(None),
    bound_via: str | None = Query(
        None,
        description="Filter by bound_via or alias (e.g. manual)",
    ),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    limit: int = Query(_DEFAULT_LIMIT),
    offset: int = Query(0, ge=0),
    exclude_test_chats: bool = Query(False),
    db: Session = Depends(get_db_dependency),
):
    slug = (method or "venmo").strip().lower()
    if club_id is not None:
        _get_club_or_404(db, club_id)
    limit = _clamp_limit(limit)
    dt_from = _parse_dt(from_dt)
    dt_to = _parse_dt(to_dt)

    try:
        q = db.query(GroupPaymentMethodBinding).filter(
            GroupPaymentMethodBinding.payment_method_slug == slug
        )
        if club_id is not None:
            q = q.filter(GroupPaymentMethodBinding.club_id == club_id)
        if dt_from is not None:
            q = q.filter(GroupPaymentMethodBinding.bound_at >= dt_from)
        if dt_to is not None:
            q = q.filter(GroupPaymentMethodBinding.bound_at <= dt_to)
        q = _apply_bound_via_filter(q, GroupPaymentMethodBinding.bound_via, bound_via)
        if exclude_test_chats:
            q = apply_analytics_chat_exclusion(
                db, q, GroupPaymentMethodBinding.telegram_chat_id
            )
        total = q.count()
        rows = (
            q.order_by(GroupPaymentMethodBinding.bound_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)
        raise

    club_names: dict[int, str] = {}
    variant_labels: dict[int, str] = {}
    items: list[GroupBindingRead] = []
    for row in rows:
        cid = int(row.club_id)
        if cid not in club_names:
            club = db.query(Club).filter(Club.id == cid).first()
            club_names[cid] = club.name if club else None
        vid = int(row.variant_id) if row.variant_id else None
        vlabel: str | None = None
        if vid is not None and vid not in variant_labels:
            variant = db.query(ClubPaymentTierVariant).filter(
                ClubPaymentTierVariant.id == vid
            ).first()
            variant_labels[vid] = variant.label if variant else None
        if vid is not None:
            vlabel = variant_labels.get(vid)

        title, gg_id = resolve_group_title(db, int(row.telegram_chat_id))
        items.append(
            GroupBindingRead(
                id=int(row.id),
                telegram_chat_id=int(row.telegram_chat_id),
                club_id=cid,
                club_name=club_names.get(cid),
                payment_method_slug=str(row.payment_method_slug),
                variant_id=vid,
                variant_label=vlabel,
                venmo_handle=row.venmo_handle,
                bound_via=str(row.bound_via),
                bound_at=row.bound_at,
                group_title=title,
                gg_player_id=gg_id,
            )
        )

    return GroupBindingListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.delete("/bindings/{binding_id}", response_model=UnbindResponse)
def delete_group_binding(
    binding_id: int,
    db: Session = Depends(get_db_dependency),
):
    try:
        exists = (
            db.query(GroupPaymentMethodBinding.id)
            .filter_by(id=int(binding_id))
            .one_or_none()
        )
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)
        raise
    if exists is None:
        raise HTTPException(404, "Binding not found")
    if not unbind_by_id(int(binding_id)):
        return UnbindResponse(ok=False, error="Could not remove binding.")
    return UnbindResponse(ok=True)


@router.get("/bindings/summary", response_model=BindingSummaryResponse)
def bindings_summary(
    method: str = Query("venmo", description="payment_method_slug"),
    club_id: int | None = Query(None),
    bound_via: str | None = Query(
        None,
        description="Filter by bound_via or alias (e.g. manual)",
    ),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    exclude_test_chats: bool = Query(False),
    db: Session = Depends(get_db_dependency),
):
    slug = (method or "venmo").strip().lower()
    if club_id is not None:
        _get_club_or_404(db, club_id)

    dt_from = _parse_dt(from_dt)
    dt_to = _parse_dt(to_dt)
    bound_via_values = _resolve_bound_via_filter(bound_via)

    try:
        binding_q = db.query(
            GroupPaymentMethodBinding.bound_via,
            func.count(GroupPaymentMethodBinding.id),
        ).filter(GroupPaymentMethodBinding.payment_method_slug == slug)
        if club_id is not None:
            binding_q = binding_q.filter(GroupPaymentMethodBinding.club_id == club_id)
        if dt_from is not None:
            binding_q = binding_q.filter(GroupPaymentMethodBinding.bound_at >= dt_from)
        if dt_to is not None:
            binding_q = binding_q.filter(GroupPaymentMethodBinding.bound_at <= dt_to)
        binding_q = _apply_bound_via_filter(
            binding_q, GroupPaymentMethodBinding.bound_via, bound_via
        )
        if exclude_test_chats:
            binding_q = apply_analytics_chat_exclusion(
                db, binding_q, GroupPaymentMethodBinding.telegram_chat_id
            )
        binding_q = binding_q.group_by(GroupPaymentMethodBinding.bound_via)

        attempt_q = db.query(PaymentMethodBindAttempt).filter(
            PaymentMethodBindAttempt.payment_method_slug == slug
        )
        if club_id is not None:
            attempt_q = attempt_q.filter(PaymentMethodBindAttempt.club_id == club_id)
        if dt_from is not None:
            attempt_q = attempt_q.filter(PaymentMethodBindAttempt.created_at >= dt_from)
        if dt_to is not None:
            attempt_q = attempt_q.filter(PaymentMethodBindAttempt.created_at <= dt_to)
        if bound_via_values and len(bound_via_values) == 1:
            only = bound_via_values[0]
            if only in _FIRST_TIME_BOUND_VIA:
                attempt_q = attempt_q.filter(PaymentMethodBindAttempt.bind_kind == only)
        if exclude_test_chats:
            attempt_q = apply_analytics_chat_exclusion(
                db, attempt_q, PaymentMethodBindAttempt.telegram_chat_id
            )

        attempts = attempt_q.all()

        bind_kind_q = db.query(
            PaymentMethodBindAttempt.bind_kind,
            func.count(PaymentMethodBindAttempt.id),
        ).filter(PaymentMethodBindAttempt.payment_method_slug == slug)
        if club_id is not None:
            bind_kind_q = bind_kind_q.filter(PaymentMethodBindAttempt.club_id == club_id)
        if dt_from is not None:
            bind_kind_q = bind_kind_q.filter(
                PaymentMethodBindAttempt.created_at >= dt_from
            )
        if dt_to is not None:
            bind_kind_q = bind_kind_q.filter(PaymentMethodBindAttempt.created_at <= dt_to)
        if exclude_test_chats:
            bind_kind_q = apply_analytics_chat_exclusion(
                db, bind_kind_q, PaymentMethodBindAttempt.telegram_chat_id
            )
        bind_kind_q = bind_kind_q.group_by(PaymentMethodBindAttempt.bind_kind)
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)
        raise

    bindings_by_via = [
        BindingViaCount(bound_via=str(row[0]), count=int(row[1])) for row in binding_q.all()
    ]
    total_bound = sum(row.count for row in bindings_by_via)
    attempts_by_bind_kind = [
        BindKindCount(bind_kind=str(row[0]), count=int(row[1]))
        for row in bind_kind_q.all()
    ]

    initiated = len(attempts)
    succeeded = sum(1 for a in attempts if a.status == "succeeded")
    expired = sum(1 for a in attempts if a.status == "expired")
    cancelled = sum(1 for a in attempts if a.status == "cancelled")
    pending = sum(1 for a in attempts if a.status == "pending")
    success_rate = (succeeded / initiated) if initiated else None

    return BindingSummaryResponse(
        payment_method_slug=slug,
        club_id=club_id,
        total_bound=total_bound,
        bindings_by_via=bindings_by_via,
        attempts_by_bind_kind=attempts_by_bind_kind,
        attempt_funnel=BindingAttemptFunnel(
            initiated=initiated,
            succeeded=succeeded,
            expired=expired,
            cancelled=cancelled,
            pending=pending,
            success_rate=success_rate,
        ),
    )


@router.get("/bind-attempts", response_model=BindAttemptListResponse)
def list_bind_attempts(
    method: str = Query("venmo"),
    club_id: int | None = Query(None),
    status: str | None = Query(None),
    from_dt: str | None = Query(None, alias="from"),
    to_dt: str | None = Query(None, alias="to"),
    limit: int = Query(_DEFAULT_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db_dependency),
):
    slug = (method or "venmo").strip().lower()
    if club_id is not None:
        _get_club_or_404(db, club_id)

    dt_from = _parse_dt(from_dt)
    dt_to = _parse_dt(to_dt)
    limit = _clamp_limit(limit)

    try:
        q = db.query(PaymentMethodBindAttempt).filter(
            PaymentMethodBindAttempt.payment_method_slug == slug
        )
        if club_id is not None:
            q = q.filter(PaymentMethodBindAttempt.club_id == club_id)
        if status and status.strip().lower() != "all":
            q = q.filter(PaymentMethodBindAttempt.status == status.strip().lower())
        if dt_from is not None:
            q = q.filter(PaymentMethodBindAttempt.created_at >= dt_from)
        if dt_to is not None:
            q = q.filter(PaymentMethodBindAttempt.created_at <= dt_to)

        total = q.count()
        rows = (
            q.order_by(PaymentMethodBindAttempt.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
    except ProgrammingError as exc:
        _raise_db_schema_error(exc)
        raise

    items: list[BindAttemptRead] = []
    for row in rows:
        title, _gg = resolve_group_title(db, int(row.telegram_chat_id))
        amount_cents = int(row.amount_cents) if row.amount_cents is not None else None
        items.append(
            BindAttemptRead(
                id=int(row.id),
                telegram_chat_id=int(row.telegram_chat_id),
                club_id=int(row.club_id),
                payment_method_slug=str(row.payment_method_slug),
                variant_id=int(row.variant_id),
                bind_kind=str(getattr(row, "bind_kind", None) or row.bound_via),
                amount_cents=amount_cents,
                amount_usd=cents_to_usd(amount_cents) if amount_cents is not None else None,
                setup_emoji=getattr(row, "setup_emoji", None),
                status=str(row.status),
                bound_via=str(row.bound_via),
                venmo_payment_id=int(row.venmo_payment_id)
                if row.venmo_payment_id
                else None,
                zelle_payment_id=int(row.zelle_payment_id)
                if row.zelle_payment_id
                else None,
                group_title=title,
                created_at=row.created_at,
                expires_at=row.expires_at,
                completed_at=row.completed_at,
            )
        )

    return BindAttemptListResponse(
        items=items, total=int(total), limit=limit, offset=offset
    )
