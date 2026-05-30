"""Stripe deposit context lookup for Zapier (Glide) Confirm Stripe Payments."""

import os

import stripe
from fastapi import APIRouter, Header, HTTPException, Query, Request

from bot.services.stripe_deposit import (
    STRIPE_WEBHOOK_SECRET_ENV,
    apply_checkout_session_webhook_event,
    construct_stripe_webhook_event,
    lookup_deposit_context_by_customer_id,
)

router = APIRouter(prefix="/api/stripe", tags=["stripe"])

LOOKUP_SECRET_ENV = "STRIPE_ZAPIER_LOOKUP_SECRET"
LOOKUP_HEADER = "x-stripe-lookup-secret"

_CHECKOUT_WEBHOOK_EVENTS = frozenset(
    {"checkout.session.completed", "checkout.session.expired"}
)


def _verify_lookup_secret(x_stripe_lookup_secret: str | None) -> None:
    expected = (os.getenv(LOOKUP_SECRET_ENV) or "").strip()
    if not expected:
        raise HTTPException(
            503,
            f"{LOOKUP_SECRET_ENV} is not configured on the server",
        )
    if not x_stripe_lookup_secret or x_stripe_lookup_secret.strip() != expected:
        raise HTTPException(401, "Invalid lookup secret")


@router.get("/deposit-context")
def deposit_context(
    customer_id: str = Query(..., description="Stripe Customer ID (cus_…)"),
    x_stripe_lookup_secret: str | None = Header(None, alias=LOOKUP_HEADER),
):
    """Return current group title and player fields for a Stripe customer (Zapier)."""
    _verify_lookup_secret(x_stripe_lookup_secret)
    ctx = lookup_deposit_context_by_customer_id(customer_id)
    if ctx is None:
        raise HTTPException(404, "No deposit mapping for this Stripe customer")
    return {
        "telegram_chat_id": ctx.telegram_chat_id,
        "group_title": ctx.group_title,
        "club_id": ctx.club_id,
        "club_name": ctx.club_name,
        "gg_player_id": ctx.gg_player_id,
        "player_display_name": ctx.player_display_name,
        "stripe_customer_id": ctx.stripe_customer_id,
    }


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Stripe webhook: update checkout session amount/status on complete or expire."""
    if not (os.getenv(STRIPE_WEBHOOK_SECRET_ENV) or "").strip():
        raise HTTPException(503, f"{STRIPE_WEBHOOK_SECRET_ENV} is not configured on the server")

    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature")
    try:
        event = construct_stripe_webhook_event(payload, sig_header)
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except stripe.SignatureVerificationError as e:
        raise HTTPException(400, "Invalid Stripe signature") from e

    if event.get("type") in _CHECKOUT_WEBHOOK_EVENTS:
        apply_checkout_session_webhook_event(event)
    return {"received": True}
