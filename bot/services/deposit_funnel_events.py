"""Persist /deposit → chips funnel steps for dashboard analytics."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError

from bot.services.flow_sessions import (
    ResolvedDepositSession,
    complete_flow_session,
    get_active_session,
    get_deposit_session_context,
    resolve_deposit_session_id,
)
from db.connection import get_db
from db.models import DepositFunnelEvent

logger = logging.getLogger(__name__)

STEP_DEPOSIT_STARTED = "deposit_started"
STEP_AMOUNT_ENTERED = "amount_entered"
STEP_UNION_CHOSEN = "union_chosen"
STEP_METHOD_CHOSEN = "method_chosen"
STEP_BIND_SETUP_COMPLETED = "bind_setup_completed"
STEP_INSTRUCTIONS_SENT = "instructions_sent"
STEP_PAYMENT_RECEIVED = "payment_received"
STEP_PAYMENT_BOUND = "payment_bound"
STEP_CHIPS_CREDITED = "chips_credited"
STEP_CHIPS_CONFIRMED = "chips_confirmed"

FUNNEL_STEP_ORDER: tuple[str, ...] = (
    STEP_DEPOSIT_STARTED,
    STEP_AMOUNT_ENTERED,
    STEP_UNION_CHOSEN,
    STEP_METHOD_CHOSEN,
    STEP_BIND_SETUP_COMPLETED,
    STEP_INSTRUCTIONS_SENT,
    STEP_PAYMENT_RECEIVED,
    STEP_PAYMENT_BOUND,
    STEP_CHIPS_CREDITED,
    STEP_CHIPS_CONFIRMED,
)


def display_funnel_step_order(
    *,
    show_union_step: bool = False,
    include_bind_setup: bool = True,
) -> tuple[str, ...]:
    """Steps shown in dashboard funnels (union/bind optional)."""
    steps: list[str] = []
    for step in FUNNEL_STEP_ORDER:
        if step == STEP_UNION_CHOSEN and not show_union_step:
            continue
        if step == STEP_BIND_SETUP_COMPLETED and not include_bind_setup:
            continue
        steps.append(step)
    return tuple(steps)


def new_deposit_session_id() -> str:
    return str(uuid.uuid4())


def record_deposit_funnel_event(
    *,
    deposit_session_id: str,
    step: str,
    telegram_chat_id: int,
    club_id: int | None = None,
    telegram_user_id: int | None = None,
    method_slug: str | None = None,
    amount_cents: int | None = None,
    is_first_deposit: bool = False,
    requires_method_setup: bool = False,
    metadata: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> None:
    """Insert one funnel step (idempotent per session + step)."""
    if step not in FUNNEL_STEP_ORDER:
        logger.warning("record_deposit_funnel_event: unknown step=%r", step)
    occurred_at = created_at
    if occurred_at is None:
        occurred_at = datetime.now(timezone.utc)
    elif occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)

    try:
        with get_db() as session:
            existing = (
                session.query(DepositFunnelEvent)
                .filter_by(
                    deposit_session_id=deposit_session_id,
                    step=step,
                )
                .one_or_none()
            )
            if existing is not None:
                return
            row = DepositFunnelEvent(
                deposit_session_id=deposit_session_id,
                step=step,
                club_id=club_id,
                telegram_user_id=telegram_user_id,
                telegram_chat_id=int(telegram_chat_id),
                method_slug=method_slug,
                amount_cents=amount_cents,
                is_first_deposit=bool(is_first_deposit),
                requires_method_setup=bool(requires_method_setup),
                metadata_json=metadata,
                created_at=occurred_at,
            )
            session.add(row)
    except IntegrityError:
        pass
    except Exception:
        logger.exception(
            "record_deposit_funnel_event failed session=%s step=%s chat_id=%s",
            deposit_session_id,
            step,
            telegram_chat_id,
        )



def record_payment_funnel_from_ingest(
    *,
    telegram_chat_id: int | None,
    club_id: int | None,
    amount_cents: int,
    payment_method_slug: str,
    payment_id: int,
    auto_bound: bool,
    bind_attempt_id: int | None = None,
    stripe_checkout_session_id: str | None = None,
) -> None:
    """Record payment_received (+ payment_bound when auto-bound) for a correlated session."""
    if telegram_chat_id is None:
        return
    resolved = resolve_deposit_session_id(
        bind_attempt_id=bind_attempt_id,
        stripe_checkout_session_id=stripe_checkout_session_id,
        telegram_chat_id=int(telegram_chat_id),
    )
    if resolved is None:
        return
    meta = {
        "payment_method_slug": payment_method_slug,
        "payment_id": int(payment_id),
        "auto_bound": bool(auto_bound),
    }
    common = {
        "deposit_session_id": resolved.deposit_session_id,
        "telegram_chat_id": int(telegram_chat_id),
        "club_id": club_id if club_id is not None else resolved.club_id,
        "telegram_user_id": resolved.telegram_user_id,
        "is_first_deposit": resolved.is_first_deposit,
        "requires_method_setup": resolved.requires_method_setup,
        "method_slug": payment_method_slug,
        "amount_cents": int(amount_cents),
    }
    record_deposit_funnel_event(
        **common,
        step=STEP_PAYMENT_RECEIVED,
        metadata=meta,
    )
    if auto_bound:
        record_deposit_funnel_event(
            **common,
            step=STEP_PAYMENT_BOUND,
            metadata={**meta, "bound_via": "auto"},
        )


def _amount_cents_for_payment(payment_method_slug: str, payment_id: int) -> int | None:
    from db.models import (
        CashAppPayment,
        CryptoPayment,
        PayPalPayment,
        StripeCheckoutSession,
        VenmoPayment,
        ZellePayment,
    )

    models = {
        "venmo": VenmoPayment,
        "zelle": ZellePayment,
        "cashapp": CashAppPayment,
        "paypal": PayPalPayment,
        "crypto": CryptoPayment,
        "stripe": StripeCheckoutSession,
    }
    model = models.get(payment_method_slug)
    if model is None:
        return None
    with get_db() as session:
        row = session.query(model).filter_by(id=int(payment_id)).one_or_none()
        if row is None:
            return None
        cents = getattr(row, "amount_cents", None)
        return int(cents) if cents is not None else None


def record_payment_funnel_on_manual_bind(
    *,
    telegram_chat_id: int,
    club_id: int | None,
    amount_cents: int,
    payment_method_slug: str,
    payment_id: int,
    bind_attempt_id: int | None = None,
) -> None:
    """Record payment steps when staff binds an unbound payment to a group."""
    resolved = resolve_deposit_session_id(
        bind_attempt_id=bind_attempt_id,
        telegram_chat_id=int(telegram_chat_id),
    )
    if resolved is None:
        return
    meta = {
        "payment_method_slug": payment_method_slug,
        "payment_id": int(payment_id),
        "auto_bound": False,
        "bound_via": "manual",
    }
    common = {
        "deposit_session_id": resolved.deposit_session_id,
        "telegram_chat_id": int(telegram_chat_id),
        "club_id": club_id if club_id is not None else resolved.club_id,
        "telegram_user_id": resolved.telegram_user_id,
        "is_first_deposit": resolved.is_first_deposit,
        "requires_method_setup": resolved.requires_method_setup,
        "method_slug": payment_method_slug,
        "amount_cents": int(amount_cents),
    }
    record_deposit_funnel_event(
        **common,
        step=STEP_PAYMENT_RECEIVED,
        metadata=meta,
    )
    record_deposit_funnel_event(
        **common,
        step=STEP_PAYMENT_BOUND,
        metadata=meta,
    )


def record_payment_funnel_on_manual_bind_from_event(
    *,
    payment_method_slug: str,
    payment_id: int,
    telegram_chat_id: int,
    club_id: int | None,
    bind_attempt_id: int | None = None,
) -> None:
    amount_cents = _amount_cents_for_payment(payment_method_slug, payment_id)
    if amount_cents is None:
        return
    record_payment_funnel_on_manual_bind(
        telegram_chat_id=int(telegram_chat_id),
        club_id=club_id,
        amount_cents=amount_cents,
        payment_method_slug=payment_method_slug,
        payment_id=payment_id,
        bind_attempt_id=bind_attempt_id,
    )


def record_chips_credited_funnel(
    *,
    telegram_chat_id: int,
    club_id: int | None,
    amount_cents: int,
    method_slug: str | None = None,
    payment_id: int | None = None,
    payment_method_slug: str | None = None,
    chip_add_status: str | None = None,
    path: str,
    deposit_session_id: str | None = None,
    bind_attempt_id: int | None = None,
    stripe_checkout_session_id: str | None = None,
    complete_session: bool = True,
) -> None:
    """Record chips_credited (+ chips_confirmed) for a deposit session."""
    resolved: ResolvedDepositSession | None = None
    if deposit_session_id:
        resolved = get_deposit_session_context(str(deposit_session_id))
    elif path == "manual_add":
        active = get_active_session(int(telegram_chat_id))
        if active is None or active.flow_type != "deposit":
            return
        resolved = get_deposit_session_context(active.session_uuid)
    else:
        resolved = resolve_deposit_session_id(
            bind_attempt_id=bind_attempt_id,
            stripe_checkout_session_id=stripe_checkout_session_id,
            telegram_chat_id=int(telegram_chat_id),
        )

    if resolved is None:
        return

    slug = method_slug or payment_method_slug
    meta: dict[str, Any] = {
        "path": path,
        "chip_add_status": chip_add_status,
    }
    if payment_id is not None and payment_method_slug:
        meta["payment_method_slug"] = payment_method_slug
        meta["payment_id"] = int(payment_id)
    common = {
        "deposit_session_id": resolved.deposit_session_id,
        "telegram_chat_id": int(telegram_chat_id),
        "club_id": club_id if club_id is not None else resolved.club_id,
        "telegram_user_id": resolved.telegram_user_id,
        "is_first_deposit": resolved.is_first_deposit,
        "requires_method_setup": resolved.requires_method_setup,
        "method_slug": slug,
        "amount_cents": int(amount_cents),
    }
    record_deposit_funnel_event(
        **common,
        step=STEP_CHIPS_CREDITED,
        metadata=meta,
    )
    record_deposit_funnel_event(
        **common,
        step=STEP_CHIPS_CONFIRMED,
        metadata=meta,
    )
    if complete_session:
        complete_flow_session(str(resolved.deposit_session_id))
