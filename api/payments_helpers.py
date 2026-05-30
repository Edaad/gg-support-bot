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
    club_id: int | None = None,
    fallback_gg_player_id: str | None = None,
    fallback_player_display_name: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Return (group_title, gg_player_id, player_display_name) for a chat.

    player_display_name prefers player_details.gg_nickname when club_id and gg_player_id are known.
    """
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
    player_display_name = fallback_player_display_name
    if title:
        from bot.services.player_details import parse_group_title_parts

        parsed = parse_group_title_parts(title)
        if parsed:
            gg_player_id = parsed.gg_player_id or gg_player_id
            tail = (parsed.tail or "").strip()
            if tail:
                player_display_name = tail

    if club_id is not None:
        nick = lookup_gg_nickname(session, club_id, gg_player_id)
        if nick:
            player_display_name = nick

    return title, gg_player_id, player_display_name


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
