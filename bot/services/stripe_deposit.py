"""Stripe Checkout for per-request debit-card deposits (one customer per group chat)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import stripe
from sqlalchemy.exc import IntegrityError

from bot.services.club import get_group_title_for_chat, update_group_name
from bot.services.group_chat_invite_links import resolve_group_chat_notification_url
from bot.services.payment_group_notify import notify_player_group_payment_received
from bot.services.payment_auto_deposit import schedule_auto_deposit_from_payment
from bot.services.player_details import parse_group_title_parts
from bot.services.venmo_payments import (
    escape_notification_html,
    format_amount_display,
    send_telegram_notification,
)
from db.connection import get_db
from db.models import Club, ClubPaymentMethod, StripeCheckoutSession, StripeCustomer
from notification.formatting import format_group_chat_line, format_player_id_line

logger = logging.getLogger(__name__)

STRIPE_SECRET_KEY_ENV = "STRIPE_SECRET_KEY"
STRIPE_TEST_SECRET_KEY_ENV = "STRIPE_TEST_SECRET_KEY"
STRIPE_SUCCESS_URL_ENV = "STRIPE_CHECKOUT_SUCCESS_URL"
STRIPE_CANCEL_URL_ENV = "STRIPE_CHECKOUT_CANCEL_URL"
STRIPE_WEBHOOK_SECRET_ENV = "STRIPE_WEBHOOK_SECRET"
DEFAULT_SUCCESS_URL = "https://stripe.com/docs/payments/checkout"
DEFAULT_CANCEL_URL = "https://stripe.com"

TERMINAL_CHECKOUT_STATUSES = frozenset({"complete", "expired"})

# Player chooses amount on the Stripe Checkout page (USD cents).
STRIPE_CHECKOUT_MIN_CENTS = 2000  # $20
STRIPE_CHECKOUT_MAX_CENTS = 10000  # $100
STRIPE_CHECKOUT_PRESET_CENTS = 5000  # $50 default shown on checkout


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


def resolve_stripe_secret_key() -> str:
    """Return Stripe secret key from env (STRIPE_TEST_SECRET_KEY on test worker, else STRIPE_SECRET_KEY)."""
    from bot.runtime_config import is_test_bot_worker

    if is_test_bot_worker():
        for key in (STRIPE_TEST_SECRET_KEY_ENV, STRIPE_SECRET_KEY_ENV):
            val = (os.getenv(key) or "").strip()
            if val:
                return val
        return ""
    return (os.getenv(STRIPE_SECRET_KEY_ENV) or "").strip()


def stripe_configured() -> bool:
    return bool(resolve_stripe_secret_key())


def _stripe_client() -> None:
    key = resolve_stripe_secret_key()
    if not key:
        raise RuntimeError(
            f"{STRIPE_SECRET_KEY_ENV} (or {STRIPE_TEST_SECRET_KEY_ENV} on test bot) is not set"
        )
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


def _usd_to_cents(value: Decimal | float | int | str | None) -> int | None:
    if value is None:
        return None
    try:
        dollars = Decimal(str(value))
    except Exception:
        return None
    if dollars <= 0:
        return None
    return int((dollars * 100).to_integral_value())


def resolve_checkout_amount_cents(
    *,
    min_usd: Decimal | float | int | str | None = None,
    max_usd: Decimal | float | int | str | None = None,
    preset_usd: Decimal | float | int | str | None = None,
) -> tuple[int, int, int]:
    """Return (min_cents, max_cents, preset_cents) for Stripe custom_unit_amount."""
    min_c = _usd_to_cents(min_usd)
    max_c = _usd_to_cents(max_usd)
    if min_c is None:
        min_c = STRIPE_CHECKOUT_MIN_CENTS
    if max_c is None:
        max_c = STRIPE_CHECKOUT_MAX_CENTS
    if min_c > max_c:
        max_c = min_c
    preset_c = _usd_to_cents(preset_usd)
    if preset_c is not None:
        preset = max(min_c, min(max_c, preset_c))
    elif min_usd is None and max_usd is None:
        preset = STRIPE_CHECKOUT_PRESET_CENTS
    else:
        preset = (min_c + max_c) // 2
    return min_c, max_c, preset


def _create_custom_amount_price_id(
    group_title: str | None,
    *,
    no_minimum: bool = False,
    min_cents: int | None = None,
    max_cents: int | None = None,
    preset_cents: int | None = None,
) -> str:
    """Create a one-time Price with custom amount.

    When no_minimum is True, omits min/max so the player can enter any amount.
    """
    product_data = _checkout_product_data(group_title)
    if no_minimum:
        logger.info("stripe: creating product+price custom_unit_amount (no minimum)")
        custom_unit_amount: dict = {"enabled": True}
    else:
        if min_cents is None or max_cents is None or preset_cents is None:
            min_cents, max_cents, preset_cents = resolve_checkout_amount_cents()
        logger.info(
            "stripe: creating product+price custom_unit_amount min=%s max=%s preset=%s",
            min_cents,
            max_cents,
            preset_cents,
        )
        custom_unit_amount = {
            "enabled": True,
            "minimum": min_cents,
            "maximum": max_cents,
            "preset": preset_cents,
        }
    product = stripe.Product.create(
        name=product_data["name"],
        description=product_data.get("description"),
    )
    price = stripe.Price.create(
        currency="usd",
        product=product.id,
        custom_unit_amount=custom_unit_amount,
    )
    logger.info("stripe: created price_id=%s product_id=%s", price.id, product.id)
    return str(price.id)


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
    logger.info(
        "stripe: get_or_create_customer chat_id=%s club_id=%s title=%r gg_player_id=%s",
        cid,
        club,
        (title or "")[:80],
        gg_player_id,
    )

    with get_db() as session:
        row = (
            session.query(StripeCustomer)
            .filter(StripeCustomer.telegram_chat_id == cid)
            .one_or_none()
        )
        if row is not None:
            logger.info("stripe: reusing customer_id=%s", row.stripe_customer_id)
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

    try:
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
    except IntegrityError:
        logger.warning(
            "stripe: duplicate stripe_customers insert chat_id=%s; reusing existing row",
            cid,
        )
        with get_db() as session:
            row = (
                session.query(StripeCustomer)
                .filter(StripeCustomer.telegram_chat_id == cid)
                .one_or_none()
            )
            if row is None:
                raise
            return str(row.stripe_customer_id)

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
    payment_method_id: int | None = None,
    group_title: str | None = None,
    no_minimum: bool = False,
    checkout_min_usd: Decimal | float | int | str | None = None,
    checkout_max_usd: Decimal | float | int | str | None = None,
    checkout_preset_usd: Decimal | float | int | str | None = None,
) -> StripeCheckoutResult:
    """Create a Checkout Session where the player picks amount on Stripe.

    Always attaches the stored Stripe Customer (customer=cus_...); no guest checkout.
    Do not pass customer_creation on Session.create.

    When no_minimum is True, no lower/upper bound is enforced on the checkout page.
    """
    _stripe_client()
    cid = int(telegram_chat_id)
    club = int(club_id)
    logger.info(
        "stripe: create_checkout_session start chat_id=%s club_id=%s payment_method_id=%s no_minimum=%s",
        cid,
        club,
        payment_method_id,
        no_minimum,
    )
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

    success_url = (
        os.getenv(STRIPE_SUCCESS_URL_ENV) or DEFAULT_SUCCESS_URL
    ).strip()
    cancel_url = (os.getenv(STRIPE_CANCEL_URL_ENV) or DEFAULT_CANCEL_URL).strip()

    session_metadata: dict[str, str] = {
        "telegram_chat_id": str(cid),
        "club_id": str(club),
    }
    if payment_method_id is not None:
        session_metadata["payment_method_id"] = str(int(payment_method_id))
    if gg_player_id:
        session_metadata["gg_player_id"] = gg_player_id
    if effective_title:
        session_metadata["group_title_snapshot"] = effective_title[:500]

    min_cents = max_cents = preset_cents = None
    if not no_minimum:
        min_cents, max_cents, preset_cents = resolve_checkout_amount_cents(
            min_usd=checkout_min_usd,
            max_usd=checkout_max_usd,
            preset_usd=checkout_preset_usd,
        )
    price_id = _create_custom_amount_price_id(
        effective_title,
        no_minimum=no_minimum,
        min_cents=min_cents,
        max_cents=max_cents,
        preset_cents=preset_cents,
    )
    logger.info("stripe: creating checkout session customer=%s price=%s", stripe_customer_id, price_id)
    checkout = stripe.checkout.Session.create(
        customer=stripe_customer_id,
        mode="payment",
        client_reference_id=str(cid),
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=session_metadata,
        line_items=[{"price": price_id, "quantity": 1}],
    )
    session_id = str(checkout.id)
    checkout_url = str(checkout.url or "")
    if not checkout_url:
        raise RuntimeError("Stripe Checkout Session returned no URL")

    # Paid deposits are recorded when Stripe sends checkout.session.completed (webhook).
    logger.info(
        "stripe checkout created chat_id=%s session_id=%s url_len=%s (custom $20-$100)",
        cid,
        session_id,
        len(checkout_url),
    )
    return StripeCheckoutResult(
        checkout_url=checkout_url,
        session_id=session_id,
        customer_id=stripe_customer_id,
    )


def _extract_payment_intent_id(value: Any) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, dict):
        pid = value.get("id")
        return str(pid) if pid else None
    return str(value)


def mark_checkout_session_paid(
    row: StripeCheckoutSession,
    *,
    amount_total: Any = None,
    payment_intent: Any = None,
    when: datetime | None = None,
) -> None:
    """Set DB row to completed paid state."""
    now = when or datetime.now(timezone.utc)
    if amount_total is not None:
        row.amount_cents = int(amount_total)
    row.status = "complete"
    row.completed_at = now
    row.updated_at = now
    pi = _extract_payment_intent_id(payment_intent)
    if pi:
        row.stripe_payment_intent_id = pi


def _metadata_int(meta: dict[str, Any], key: str) -> Optional[int]:
    raw = meta.get(key)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def record_completed_checkout_payment(
    checkout_obj: dict[str, Any],
    *,
    when: datetime | None = None,
) -> bool:
    """Insert a completed payment row from a Stripe Checkout Session object (webhook payload)."""
    session_id = str(checkout_obj.get("id") or "").strip()
    if not session_id:
        return False

    now = when or datetime.now(timezone.utc)
    meta = checkout_obj.get("metadata") or {}
    chat_id = _metadata_int(meta, "telegram_chat_id")
    if chat_id is None and checkout_obj.get("client_reference_id"):
        try:
            chat_id = int(checkout_obj["client_reference_id"])
        except (TypeError, ValueError):
            chat_id = None
    club_id = _metadata_int(meta, "club_id")
    stripe_customer_id = str(checkout_obj.get("customer") or "").strip()

    if chat_id is None or club_id is None or not stripe_customer_id:
        logger.warning(
            "stripe webhook: cannot record payment — missing chat_id=%s club_id=%s customer=%r session=%s",
            chat_id,
            club_id,
            stripe_customer_id or None,
            session_id,
        )
        return False

    amount_total = checkout_obj.get("amount_total")
    amount_cents = int(amount_total) if amount_total is not None else 0
    payment_method_id = _metadata_int(meta, "payment_method_id")
    pi = _extract_payment_intent_id(checkout_obj.get("payment_intent"))

    with get_db() as db:
        existing = (
            db.query(StripeCheckoutSession)
            .filter(StripeCheckoutSession.stripe_checkout_session_id == session_id)
            .one_or_none()
        )
        if existing is not None:
            if existing.status == "complete":
                return False
            mark_checkout_session_paid(
                existing,
                amount_total=amount_total,
                payment_intent=checkout_obj.get("payment_intent"),
                when=now,
            )
            db.flush()
            return True

        row = StripeCheckoutSession(
            stripe_checkout_session_id=session_id,
            stripe_customer_id=stripe_customer_id,
            telegram_chat_id=chat_id,
            club_id=club_id,
            amount_cents=amount_cents,
            currency=str(checkout_obj.get("currency") or "usd"),
            status="complete",
            payment_method_id=payment_method_id,
            stripe_payment_intent_id=pi,
            completed_at=now,
            updated_at=now,
        )
        try:
            db.add(row)
            db.flush()
        except IntegrityError:
            logger.info("stripe webhook: duplicate insert for session %s (ignored)", session_id)
            return False

    logger.info(
        "stripe webhook: recorded completed payment session=%s chat_id=%s amount_cents=%s",
        session_id,
        chat_id,
        amount_cents,
    )
    return True


def format_stripe_method_label(method_name: str | None) -> str:
    """Display label for Stripe deposit notifications (e.g. Stripe Cashapp)."""
    raw = (method_name or "").strip()
    if not raw:
        return "Stripe"
    if raw.lower().startswith("stripe"):
        return raw
    return f"Stripe {raw}"


def _resolve_stripe_method_label(club_id: int, payment_method_id: int | None) -> str:
    if payment_method_id is None:
        return "Stripe"
    with get_db() as session:
        row = (
            session.query(ClubPaymentMethod)
            .filter_by(id=int(payment_method_id), club_id=int(club_id))
            .one_or_none()
        )
        if row is None:
            return "Stripe"
        return format_stripe_method_label(row.name)


def format_stripe_payment_notification_text(
    *,
    club_name: str,
    group_title: str,
    amount_cents: int,
    method_label: str,
    club_id: int | None = None,
    telegram_chat_id: int | None = None,
    group_chat_url: str | None = None,
) -> str:
    lines = [
        f"🔔 {escape_notification_html(club_name)} Payment Notification",
        "",
        format_group_chat_line(
            group_title=group_title,
            telegram_chat_id=telegram_chat_id,
            group_chat_url=group_chat_url,
        ),
    ]
    player_line = format_player_id_line(group_title)
    if player_line:
        lines.append(player_line)
    lines.extend(
        [
            "",
            f"Amount: {format_amount_display(amount_cents, bold=True)}",
            f"Method: {escape_notification_html(method_label)}",
        ]
    )
    from bot.services.payment_auto_deposit import append_creator_club_staff_footer

    body = "\n".join(lines)
    return append_creator_club_staff_footer(
        body,
        club_id=int(club_id) if club_id is not None else None,
        telegram_chat_id=int(telegram_chat_id) if telegram_chat_id is not None else None,
        auto_bound=True,
    )


async def notify_stripe_payment_completed(checkout_obj: dict[str, Any]) -> None:
    """Post staff Telegram notification for a completed Stripe checkout."""
    meta = checkout_obj.get("metadata") or {}
    chat_id = _metadata_int(meta, "telegram_chat_id")
    if chat_id is None and checkout_obj.get("client_reference_id"):
        try:
            chat_id = int(checkout_obj["client_reference_id"])
        except (TypeError, ValueError):
            chat_id = None
    club_id = _metadata_int(meta, "club_id")
    if chat_id is None or club_id is None:
        return

    amount_total = checkout_obj.get("amount_total")
    amount_cents = int(amount_total) if amount_total is not None else 0
    payment_method_id = _metadata_int(meta, "payment_method_id")

    group_title, _ = get_group_title_for_chat(int(chat_id))
    group_title = (group_title or meta.get("group_title_snapshot") or "").strip()
    if not group_title:
        group_title = f"chat {chat_id}"

    with get_db() as session:
        club = session.query(Club).filter_by(id=int(club_id)).one_or_none()
        club_name = (club.name if club else "").strip() or "Club"

    method_label = _resolve_stripe_method_label(int(club_id), payment_method_id)
    group_chat_url = await resolve_group_chat_notification_url(
        telegram_chat_id=int(chat_id),
        group_title=group_title,
        club_id=int(club_id),
    )
    text = format_stripe_payment_notification_text(
        club_name=club_name,
        group_title=group_title,
        amount_cents=amount_cents,
        method_label=method_label,
        club_id=int(club_id),
        telegram_chat_id=int(chat_id),
        group_chat_url=group_chat_url,
    )
    await send_telegram_notification(text)
    await notify_player_group_payment_received(
        telegram_chat_id=int(chat_id),
        amount_cents=amount_cents,
    )

    session_id = str(checkout_obj.get("id") or "").strip()
    stripe_payment_id: int | None = None
    if session_id:
        with get_db() as session:
            row = (
                session.query(StripeCheckoutSession)
                .filter(StripeCheckoutSession.stripe_checkout_session_id == session_id)
                .one_or_none()
            )
            if row is not None:
                stripe_payment_id = int(row.id)
    if stripe_payment_id is None and session_id:
        stripe_payment_id = abs(hash(f"stripe:{session_id}")) % (2**31 - 1)

    if stripe_payment_id is not None:
        schedule_auto_deposit_from_payment(
            club_id=int(club_id),
            telegram_chat_id=int(chat_id),
            amount_cents=amount_cents,
            auto_bound=True,
            payment_method_slug="stripe",
            payment_id=stripe_payment_id,
            group_title=group_title,
        )

    logger.info(
        "stripe webhook: notification sent chat_id=%s club_id=%s amount_cents=%s method=%r",
        chat_id,
        club_id,
        amount_cents,
        method_label,
    )


def construct_stripe_webhook_event(payload: bytes, sig_header: str | None) -> dict[str, Any]:
    """Verify Stripe-Signature and return parsed webhook event."""
    secret = (os.getenv(STRIPE_WEBHOOK_SECRET_ENV) or "").strip()
    if not secret:
        raise RuntimeError(f"{STRIPE_WEBHOOK_SECRET_ENV} is not configured")
    if not sig_header:
        raise ValueError("Missing Stripe-Signature header")
    return stripe.Webhook.construct_event(payload, sig_header, secret)


def apply_checkout_session_webhook_event(event: dict[str, Any]) -> bool:
    """Record a completed Stripe deposit from checkout.session.completed webhook."""
    event_type = event.get("type") or ""
    if event_type not in (
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    ):
        return False
    obj = (event.get("data") or {}).get("object") or {}
    return record_completed_checkout_payment(obj)


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
