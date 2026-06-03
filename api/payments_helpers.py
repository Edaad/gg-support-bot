"""Shared helpers for payments dashboard API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from db.models import (
    ClubPaymentMethod,
    ClubPaymentTier,
    ClubPaymentTierVariant,
    Group,
    PlayerDetails,
    StripeCheckoutSession,
    StripeCustomer,
    SupportGroupChat,
    VenmoPayment,
)


def lookup_gg_nickname(
    session: Session, club_id: int, gg_player_id: str | None
) -> str | None:
    if not gg_player_id or not str(gg_player_id).strip():
        return None
    row = (
        session.query(PlayerDetails.gg_nickname)
        .filter(
            PlayerDetails.club_id == int(club_id),
            PlayerDetails.gg_player_id == str(gg_player_id).strip(),
        )
        .first()
    )
    if not row or not row[0]:
        return None
    nick = str(row[0]).strip()
    return nick or None


def resolve_group_title(
    session: Session,
    telegram_chat_id: int,
    *,
    fallback_gg_player_id: str | None = None,
) -> tuple[str | None, str | None]:
    """Return (group_title, gg_player_id) for a chat."""
    cid = int(telegram_chat_id)
    group = session.query(Group).filter(Group.chat_id == cid).first()
    title: str | None = None
    if group and (group.name or "").strip():
        title = group.name.strip()
    else:
        sgc = (
            session.query(SupportGroupChat)
            .filter(SupportGroupChat.telegram_chat_id == cid)
            .order_by(SupportGroupChat.created_at.desc())
            .first()
        )
        if sgc and (sgc.telegram_chat_title or "").strip():
            title = sgc.telegram_chat_title.strip()

    gg_player_id = fallback_gg_player_id
    if title:
        from bot.services.player_details import parse_group_title_parts

        parsed = parse_group_title_parts(title)
        if parsed:
            gg_player_id = parsed.gg_player_id or gg_player_id

    return title, gg_player_id


def stripe_dashboard_session_url(session_id: str) -> str:
    return f"https://dashboard.stripe.com/checkout/sessions/{session_id}"


def stripe_dashboard_payment_url(payment_intent_id: str | None) -> str | None:
    if not payment_intent_id:
        return None
    return f"https://dashboard.stripe.com/payments/{payment_intent_id}"


def list_stripe_deposit_methods(session: Session, club_id: int) -> list[dict]:
    """Deposit methods for a club that have Stripe checkout enabled on a tier or variant."""
    methods = (
        session.query(ClubPaymentMethod)
        .filter(
            ClubPaymentMethod.club_id == club_id,
            ClubPaymentMethod.direction == "deposit",
        )
        .options(
            joinedload(ClubPaymentMethod.tiers).joinedload(ClubPaymentTier.variants),
        )
        .order_by(ClubPaymentMethod.sort_order, ClubPaymentMethod.id)
        .all()
    )
    out: list[dict] = []
    for method in methods:
        has_stripe = False
        for tier in method.tiers or []:
            if _tier_has_stripe(tier):
                has_stripe = True
                break
            for variant in tier.variants or []:
                if _variant_has_stripe(variant):
                    has_stripe = True
                    break
            if has_stripe:
                break
        if has_stripe:
            out.append({"id": method.id, "name": method.name, "slug": method.slug})
    return out


def _tier_has_stripe(tier: ClubPaymentTier) -> bool:
    if not tier.use_group_checkout_link:
        return False
    return (tier.group_checkout_provider or "stripe").strip().lower() == "stripe"


def _variant_has_stripe(variant: ClubPaymentTierVariant) -> bool:
    if variant.use_group_checkout_link is not True:
        return False
    return (variant.group_checkout_provider or "stripe").strip().lower() == "stripe"


def resolve_method_display(
    session: Session,
    club_id: int,
    payment_method_id: int | None,
) -> tuple[str | None, str | None]:
    if payment_method_id is None:
        return "Manual (/stripe)", "stripe"
    row = (
        session.query(ClubPaymentMethod)
        .filter(
            ClubPaymentMethod.id == payment_method_id,
            ClubPaymentMethod.club_id == club_id,
        )
        .one_or_none()
    )
    if row is None:
        return f"Method #{payment_method_id}", None
    return row.name, row.slug


def customer_total_deposited_cents(session: Session, club_id: int) -> dict[str, int]:
    rows = (
        session.query(
            StripeCheckoutSession.stripe_customer_id,
            func.coalesce(func.sum(StripeCheckoutSession.amount_cents), 0),
        )
        .filter(
            StripeCheckoutSession.club_id == club_id,
            StripeCheckoutSession.status == "complete",
        )
        .group_by(StripeCheckoutSession.stripe_customer_id)
        .all()
    )
    return {str(customer_id): int(total or 0) for customer_id, total in rows}


def apply_customer_search(query, q: str | None):
    if not q or not q.strip():
        return query
    term = f"%{q.strip()}%"
    return query.filter(
        or_(
            StripeCustomer.stripe_customer_id.ilike(term),
            StripeCustomer.gg_player_id.ilike(term),
            StripeCustomer.player_display_name.ilike(term),
        )
    )


def apply_session_filters(
    query,
    *,
    club_id: int,
    status: str | None,
    method_id: int | None,
    manual_only: bool,
    from_dt: datetime | None,
    to_dt: datetime | None,
):
    query = query.filter(StripeCheckoutSession.club_id == club_id)
    if status and status.strip().lower() != "all":
        query = query.filter(StripeCheckoutSession.status == status.strip().lower())
    if manual_only:
        query = query.filter(StripeCheckoutSession.payment_method_id.is_(None))
    elif method_id is not None:
        query = query.filter(StripeCheckoutSession.payment_method_id == method_id)
    if from_dt is not None:
        query = query.filter(StripeCheckoutSession.created_at >= from_dt)
    if to_dt is not None:
        query = query.filter(StripeCheckoutSession.created_at <= to_dt)
    return query


def cents_to_usd(amount_cents: int) -> Decimal:
    return (Decimal(amount_cents) / Decimal(100)).quantize(Decimal("0.01"))


def venmo_payment_status(payment: VenmoPayment) -> str:
    return "bound" if payment.telegram_chat_id is not None else "unbound"


def apply_venmo_payment_filters(
    query,
    *,
    club_id: int,
    status: str | None,
    from_dt: datetime | None,
    to_dt: datetime | None,
    include_test: bool,
    q: str | None,
):
    if not include_test:
        query = query.filter(VenmoPayment.is_test.is_(False))

    status_norm = (status or "all").strip().lower()
    if status_norm == "bound":
        query = query.filter(
            VenmoPayment.telegram_chat_id.isnot(None),
            VenmoPayment.club_id == club_id,
        )
    elif status_norm == "unbound":
        query = query.filter(VenmoPayment.telegram_chat_id.is_(None))
    else:
        query = query.filter(
            or_(
                VenmoPayment.telegram_chat_id.is_(None),
                VenmoPayment.club_id == club_id,
            )
        )

    if from_dt is not None:
        query = query.filter(VenmoPayment.created_at >= from_dt)
    if to_dt is not None:
        query = query.filter(VenmoPayment.created_at <= to_dt)

    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(
                VenmoPayment.payer_name.ilike(term),
                VenmoPayment.venmo_handle.ilike(term),
                VenmoPayment.bound_group_title_at_bind.ilike(term),
            )
        )
    return query


def apply_venmo_payer_search(query, q: str | None):
    if not q or not q.strip():
        return query
    term = f"%{q.strip()}%"
    return query.filter(
        or_(
            VenmoPayment.payer_name.ilike(term),
            VenmoPayment.venmo_handle.ilike(term),
        )
    )


def list_venmo_payer_aggregates(session: Session, club_id: int, q: str | None):
    """Return grouped payer rows: payer_name, venmo_handle, totals, latest chat."""
    base = (
        session.query(
            VenmoPayment.payer_name,
            VenmoPayment.venmo_handle,
            func.coalesce(func.sum(VenmoPayment.amount_cents), 0).label("total_cents"),
            func.count(VenmoPayment.id).label("payment_count"),
            func.max(VenmoPayment.created_at).label("last_payment_at"),
            func.max(VenmoPayment.telegram_chat_id).label("telegram_chat_id"),
        )
        .filter(
            VenmoPayment.club_id == club_id,
            VenmoPayment.telegram_chat_id.isnot(None),
            VenmoPayment.is_test.is_(False),
        )
        .group_by(VenmoPayment.payer_name, VenmoPayment.venmo_handle)
    )
    base = apply_venmo_payer_search(base, q)
    return base.order_by(func.max(VenmoPayment.created_at).desc())


def build_venmo_payment_read(session: Session, payment: VenmoPayment) -> dict:
    title: str | None = None
    gg_id: str | None = None
    if payment.telegram_chat_id is not None:
        title, gg_id = resolve_group_title(session, int(payment.telegram_chat_id))
    club_id = payment.club_id
    return {
        "id": payment.id,
        "payer_name": payment.payer_name,
        "venmo_handle": payment.venmo_handle,
        "amount_cents": payment.amount_cents,
        "amount_usd": cents_to_usd(payment.amount_cents),
        "goods_or_services": payment.goods_or_services,
        "paid_at": payment.paid_at,
        "group_title": title,
        "gg_player_id": gg_id,
        "gg_nickname": lookup_gg_nickname(session, club_id, gg_id) if club_id else None,
        "club_id": club_id,
        "telegram_chat_id": payment.telegram_chat_id,
        "status": venmo_payment_status(payment),
        "auto_bound": payment.auto_bound,
        "is_test": payment.is_test,
        "created_at": payment.created_at,
        "bound_at": payment.bound_at,
    }
