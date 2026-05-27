"""Stripe Checkout for per-request debit-card deposits (one customer per group chat)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import stripe

from bot.services.club import get_group_title_for_chat, update_group_name
from bot.services.player_details import parse_group_title_parts
from db.connection import get_db
from db.models import Club, StripeCheckoutSession, StripeCustomer

logger = logging.getLogger(__name__)

STRIPE_SECRET_KEY_ENV = "STRIPE_SECRET_KEY"
STRIPE_SUCCESS_URL_ENV = "STRIPE_CHECKOUT_SUCCESS_URL"
STRIPE_CANCEL_URL_ENV = "STRIPE_CHECKOUT_CANCEL_URL"
DEFAULT_SUCCESS_URL = "https://stripe.com/docs/payments/checkout"
DEFAULT_CANCEL_URL = "https://stripe.com"


@dataclass(frozen=True)
class StripeCheckoutResult:
    checkout_url: str
    session_id: str
    customer_id: str


@dataclass(frozen=True)
class StripeDepositContext:
    telegram_chat_id: int
    group_title: Optional[str]
    club_id: int
    club_name: str
    gg_player_id: Optional[str]
    player_display_name: Optional[str]
    stripe_customer_id: str


def stripe_configured() -> bool:
    return bool((os.getenv(STRIPE_SECRET_KEY_ENV) or "").strip())


def _stripe_client() -> None:
    key = (os.getenv(STRIPE_SECRET_KEY_ENV) or "").strip()
    if not key:
        raise RuntimeError(f"{STRIPE_SECRET_KEY_ENV} is not set")
    stripe.api_key = key


def _title_player_fields(title: str | None) -> tuple[Optional[str], Optional[str]]:
    parsed = parse_group_title_parts(title)
    if not parsed:
        return None, None
    tail = (parsed.tail or "").strip() or None
    return parsed.gg_player_id, tail


def _checkout_product_data(group_title: str | None) -> dict[str, str]:
    data: dict[str, str] = {"name": "Club deposit"}
    if group_title:
        data["description"] = group_title[:500]
    return data


def _amount_to_cents(amount: Decimal) -> int:
    cents = (amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents)


def get_or_create_stripe_customer(
    *,
    telegram_chat_id: int,
    club_id: int,
    group_title: str | None,
) -> str:
    """Return Stripe customer id for this chat; create DB + Stripe rows if needed."""
    _stripe_client()
    cid = int(telegram_chat_id)
    club = int(club_id)
    title = (group_title or "").strip() or None
    gg_player_id, player_display_name = _title_player_fields(title)

    with get_db() as session:
        row = (
            session.query(StripeCustomer)
            .filter(StripeCustomer.telegram_chat_id == cid)
            .one_or_none()
        )
        if row is not None:
            if gg_player_id:
                row.gg_player_id = gg_player_id
            if player_display_name:
                row.player_display_name = player_display_name
            session.flush()
            return str(row.stripe_customer_id)

    metadata: dict[str, str] = {
        "telegram_chat_id": str(cid),
        "club_id": str(club),
    }
    if gg_player_id:
        metadata["gg_player_id"] = gg_player_id
    if title:
        metadata["group_title_snapshot"] = title[:500]

    customer = stripe.Customer.create(metadata=metadata)
    stripe_customer_id = str(customer.id)

    with get_db() as session:
        session.add(
            StripeCustomer(
                telegram_chat_id=cid,
                club_id=club,
                stripe_customer_id=stripe_customer_id,
                gg_player_id=gg_player_id,
                player_display_name=player_display_name,
            )
        )

    logger.info(
        "stripe customer created chat_id=%s club_id=%s customer_id=%s",
        cid,
        club,
        stripe_customer_id,
    )
    return stripe_customer_id


def create_stripe_checkout_session(
    *,
    telegram_chat_id: int,
    club_id: int,
    amount: Decimal,
    payment_method_id: int | None = None,
    group_title: str | None = None,
) -> StripeCheckoutResult:
    """Create a unique Checkout Session for one deposit request."""
    _stripe_client()
    cid = int(telegram_chat_id)
    club = int(club_id)
    if group_title:
        update_group_name(cid, group_title)
    title, _ = get_group_title_for_chat(cid)
    effective_title = (title or group_title or "").strip() or None
    gg_player_id, _ = _title_player_fields(effective_title)

    stripe_customer_id = get_or_create_stripe_customer(
        telegram_chat_id=cid,
        club_id=club,
        group_title=effective_title,
    )
    amount_cents = _amount_to_cents(amount)
    if amount_cents < 50:
        raise ValueError("Stripe checkout minimum is $0.50")

    success_url = (
        os.getenv(STRIPE_SUCCESS_URL_ENV) or DEFAULT_SUCCESS_URL
    ).strip()
    cancel_url = (os.getenv(STRIPE_CANCEL_URL_ENV) or DEFAULT_CANCEL_URL).strip()

    session_metadata: dict[str, str] = {
        "telegram_chat_id": str(cid),
        "club_id": str(club),
    }
    if gg_player_id:
        session_metadata["gg_player_id"] = gg_player_id
    if effective_title:
        session_metadata["group_title_snapshot"] = effective_title[:500]

    checkout = stripe.checkout.Session.create(
        customer=stripe_customer_id,
        mode="payment",
        client_reference_id=str(cid),
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=session_metadata,
        line_items=[
            {
                "price_data": {
                    "currency": "usd",
                    "unit_amount": amount_cents,
                    "product_data": _checkout_product_data(effective_title),
                },
                "quantity": 1,
            }
        ],
    )
    session_id = str(checkout.id)
    checkout_url = str(checkout.url or "")
    if not checkout_url:
        raise RuntimeError("Stripe Checkout Session returned no URL")

    with get_db() as session:
        session.add(
            StripeCheckoutSession(
                stripe_checkout_session_id=session_id,
                stripe_customer_id=stripe_customer_id,
                telegram_chat_id=cid,
                club_id=club,
                amount_cents=amount_cents,
                currency="usd",
                status="open",
                payment_method_id=int(payment_method_id) if payment_method_id else None,
            )
        )

    logger.info(
        "stripe checkout created chat_id=%s session_id=%s amount_cents=%s",
        cid,
        session_id,
        amount_cents,
    )
    return StripeCheckoutResult(
        checkout_url=checkout_url,
        session_id=session_id,
        customer_id=stripe_customer_id,
    )


def lookup_deposit_context_by_customer_id(
    stripe_customer_id: str,
) -> Optional[StripeDepositContext]:
    """Resolve current group title and player fields for Zapier confirm flows."""
    cust_id = (stripe_customer_id or "").strip()
    if not cust_id:
        return None

    with get_db() as session:
        row = (
            session.query(StripeCustomer)
            .filter(StripeCustomer.stripe_customer_id == cust_id)
            .one_or_none()
        )
        if row is None:
            return None
        chat_id = int(row.telegram_chat_id)
        club_id = int(row.club_id)
        stored_customer_id = str(row.stripe_customer_id)
        stored_gg_player_id = row.gg_player_id
        stored_player_display_name = row.player_display_name

    group_title, title_club_id = get_group_title_for_chat(chat_id)
    if title_club_id is not None:
        club_id = int(title_club_id)

    gg_player_id, player_display_name = _title_player_fields(group_title)
    if not gg_player_id and stored_gg_player_id:
        gg_player_id = stored_gg_player_id
    if not player_display_name and stored_player_display_name:
        player_display_name = stored_player_display_name

    with get_db() as session:
        club = session.query(Club).filter(Club.id == club_id).one_or_none()
        club_name = club.name if club else ""

    return StripeDepositContext(
        telegram_chat_id=chat_id,
        group_title=group_title,
        club_id=club_id,
        club_name=club_name,
        gg_player_id=gg_player_id,
        player_display_name=player_display_name,
        stripe_customer_id=stored_customer_id,
    )
