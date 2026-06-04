"""Per-group-chat payment method binding (first-time setup + observability)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from db.connection import get_db
from db.models import (
    ClubPaymentMethod,
    ClubPaymentTier,
    ClubPaymentTierVariant,
    GroupPaymentMethodBinding,
    PaymentMethodBindAttempt,
)

logger = logging.getLogger(__name__)

BIND_ATTEMPT_TTL_SECONDS = 600

BOUND_VIA_SPECIAL_AMOUNT = "special_amount"
BOUND_VIA_MANUAL_NOTIFICATION = "manual_notification"
BOUND_VIA_MANUAL_DASHBOARD = "manual_dashboard"
BOUND_VIA_BACKFILL = "backfill"
BOUND_VIA_TEST = "test"

ATTEMPT_STATUS_PENDING = "pending"
ATTEMPT_STATUS_SUCCEEDED = "succeeded"
ATTEMPT_STATUS_EXPIRED = "expired"
ATTEMPT_STATUS_CANCELLED = "cancelled"

_VENMO_URL_RE = re.compile(
    r"https?://(?:www\.)?venmo\.com/u/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)


def _normalize_venmo_handle(handle: str) -> str:
    raw = (handle or "").strip()
    if not raw:
        return raw
    if not raw.startswith("@"):
        raw = f"@{raw}"
    return raw.lower()


def _format_amount_display(amount_cents: int) -> str:
    dollars = Decimal(amount_cents) / Decimal(100)
    if dollars == dollars.to_integral_value():
        return f"${int(dollars):,}.00"
    return f"${dollars:,.2f}"


@dataclass(frozen=True)
class ChatMethodBinding:
    id: int
    telegram_chat_id: int
    club_id: int
    payment_method_slug: str
    variant_id: Optional[int]
    venmo_handle: Optional[str]
    bound_via: str


@dataclass(frozen=True)
class BindAttemptInfo:
    id: int
    telegram_chat_id: int
    club_id: int
    variant_id: int
    amount_cents: int
    expires_at: datetime


@dataclass(frozen=True)
class SetupMatchResult:
    attempt_id: int
    telegram_chat_id: int
    club_id: int
    variant_id: int
    group_title: str


def effective_min_cents(
    *,
    method_min: Decimal | None,
    tier_min: Decimal | None,
) -> int:
    """Minimum deposit in cents from method and tier (max of both when set)."""
    values: list[Decimal] = []
    if method_min is not None:
        values.append(method_min)
    if tier_min is not None:
        values.append(tier_min)
    if not values:
        raise ValueError("No minimum amount configured for this payment method")
    effective = max(values)
    return int((effective * 100).to_integral_value())


def extract_venmo_url(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = _VENMO_URL_RE.search(text)
    if not m:
        return None
    return f"https://venmo.com/u/{m.group(1)}"


def extract_venmo_handle_from_text(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = _VENMO_URL_RE.search(text)
    if not m:
        return None
    return _normalize_venmo_handle(m.group(1))


def variant_venmo_handle(variant_id: int) -> Optional[str]:
    with get_db() as session:
        variant = session.query(ClubPaymentTierVariant).get(int(variant_id))
        if not variant:
            return None
        for field in (variant.response_text, variant.response_caption):
            handle = extract_venmo_handle_from_text(field)
            if handle:
                return handle
    return None


def variant_response_text_by_id(variant_id: int) -> Optional[dict]:
    """Return variant response dict with ids for deposit flow."""
    with get_db() as session:
        variant = session.query(ClubPaymentTierVariant).get(int(variant_id))
        if not variant:
            return None
        from bot.services.club_payment_v2 import _variant_response_dict

        data = _variant_response_dict(variant)
        data["variant_id"] = int(variant.id)
        data["variant_label"] = variant.label
        data["tier_id"] = int(variant.tier_id) if variant.tier_id else None
        data["method_id"] = int(variant.method_id)
        return data


def is_chat_method_bound(telegram_chat_id: int, payment_method_slug: str) -> bool:
    slug = (payment_method_slug or "").strip().lower()
    with get_db() as session:
        row = (
            session.query(GroupPaymentMethodBinding.id)
            .filter_by(
                telegram_chat_id=int(telegram_chat_id),
                payment_method_slug=slug,
            )
            .one_or_none()
        )
        return row is not None


def get_chat_binding(
    telegram_chat_id: int, payment_method_slug: str
) -> Optional[ChatMethodBinding]:
    slug = (payment_method_slug or "").strip().lower()
    with get_db() as session:
        row = (
            session.query(GroupPaymentMethodBinding)
            .filter_by(
                telegram_chat_id=int(telegram_chat_id),
                payment_method_slug=slug,
            )
            .one_or_none()
        )
        if row is None:
            return None
        return ChatMethodBinding(
            id=int(row.id),
            telegram_chat_id=int(row.telegram_chat_id),
            club_id=int(row.club_id),
            payment_method_slug=str(row.payment_method_slug),
            variant_id=int(row.variant_id) if row.variant_id else None,
            venmo_handle=row.venmo_handle,
            bound_via=str(row.bound_via),
        )


def _expire_stale_pending_for_variant(session, variant_id: int) -> None:
    now = datetime.now(timezone.utc)
    (
        session.query(PaymentMethodBindAttempt)
        .filter_by(variant_id=int(variant_id), status=ATTEMPT_STATUS_PENDING)
        .filter(PaymentMethodBindAttempt.expires_at < now)
        .update(
            {PaymentMethodBindAttempt.status: ATTEMPT_STATUS_EXPIRED},
            synchronize_session=False,
        )
    )


def allocate_setup_amount_cents(
    session,
    *,
    variant_id: int,
    effective_min_cents: int,
) -> int:
    """Assign base_min - 1 cent minus count of pending attempts on this variant."""
    _expire_stale_pending_for_variant(session, variant_id)
    base_cents = int(effective_min_cents) - 1
    pending_count = (
        session.query(func.count(PaymentMethodBindAttempt.id))
        .filter_by(variant_id=int(variant_id), status=ATTEMPT_STATUS_PENDING)
        .scalar()
    )
    n = int(pending_count or 0)
    amount_cents = base_cents - n
    if amount_cents < 1:
        raise ValueError("No available setup amounts for this variant (too many pending)")
    return amount_cents


def cancel_pending_attempts_for_chat(
    session,
    *,
    telegram_chat_id: int,
    payment_method_slug: str,
) -> int:
    slug = (payment_method_slug or "").strip().lower()
    now = datetime.now(timezone.utc)
    q = (
        session.query(PaymentMethodBindAttempt)
        .filter_by(
            telegram_chat_id=int(telegram_chat_id),
            payment_method_slug=slug,
            status=ATTEMPT_STATUS_PENDING,
        )
    )
    count = q.count()
    q.update(
        {
            PaymentMethodBindAttempt.status: ATTEMPT_STATUS_CANCELLED,
            PaymentMethodBindAttempt.completed_at: now,
        },
        synchronize_session=False,
    )
    return count


def start_bind_attempt(
    *,
    telegram_chat_id: int,
    club_id: int,
    payment_method_slug: str,
    method_id: int,
    tier_id: int | None,
    variant_id: int,
    effective_min_cents: int,
    initiated_by_telegram_user_id: int | None,
) -> BindAttemptInfo:
    slug = (payment_method_slug or "").strip().lower()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=BIND_ATTEMPT_TTL_SECONDS)

    with get_db() as session:
        cancel_pending_attempts_for_chat(
            session,
            telegram_chat_id=int(telegram_chat_id),
            payment_method_slug=slug,
        )
        amount_cents = allocate_setup_amount_cents(
            session,
            variant_id=int(variant_id),
            effective_min_cents=int(effective_min_cents),
        )
        attempt = PaymentMethodBindAttempt(
            telegram_chat_id=int(telegram_chat_id),
            club_id=int(club_id),
            payment_method_slug=slug,
            method_id=int(method_id),
            tier_id=int(tier_id) if tier_id else None,
            variant_id=int(variant_id),
            amount_cents=int(amount_cents),
            status=ATTEMPT_STATUS_PENDING,
            bound_via=BOUND_VIA_SPECIAL_AMOUNT,
            initiated_by_telegram_user_id=initiated_by_telegram_user_id,
            expires_at=expires_at,
        )
        session.add(attempt)
        session.flush()
        attempt_id = int(attempt.id)

    logger.info(
        "bind_attempt started id=%s chat_id=%s variant_id=%s amount_cents=%s expires_at=%s",
        attempt_id,
        telegram_chat_id,
        variant_id,
        amount_cents,
        expires_at.isoformat(),
    )
    return BindAttemptInfo(
        id=attempt_id,
        telegram_chat_id=int(telegram_chat_id),
        club_id=int(club_id),
        variant_id=int(variant_id),
        amount_cents=int(amount_cents),
        expires_at=expires_at,
    )


def expire_attempt(attempt_id: int) -> bool:
    """Mark a pending attempt expired if still pending."""
    now = datetime.now(timezone.utc)
    with get_db() as session:
        attempt = (
            session.query(PaymentMethodBindAttempt)
            .filter_by(id=int(attempt_id), status=ATTEMPT_STATUS_PENDING)
            .one_or_none()
        )
        if attempt is None:
            return False
        attempt.status = ATTEMPT_STATUS_EXPIRED
        attempt.completed_at = now
    logger.info("bind_attempt expired id=%s", attempt_id)
    return True


def record_group_binding(
    *,
    telegram_chat_id: int,
    club_id: int,
    payment_method_slug: str,
    bound_via: str,
    variant_id: int | None = None,
    venmo_handle: str | None = None,
    bound_by_telegram_user_id: int | None = None,
    first_bind_attempt_id: int | None = None,
) -> int:
    slug = (payment_method_slug or "").strip().lower()
    handle = _normalize_venmo_handle(venmo_handle) if venmo_handle else None
    now = datetime.now(timezone.utc)

    with get_db() as session:
        row = (
            session.query(GroupPaymentMethodBinding)
            .filter_by(
                telegram_chat_id=int(telegram_chat_id),
                payment_method_slug=slug,
            )
            .one_or_none()
        )
        if row is None:
            row = GroupPaymentMethodBinding(
                telegram_chat_id=int(telegram_chat_id),
                club_id=int(club_id),
                payment_method_slug=slug,
                variant_id=int(variant_id) if variant_id else None,
                venmo_handle=handle,
                bound_via=bound_via,
                bound_at=now,
                bound_by_telegram_user_id=bound_by_telegram_user_id,
                first_bind_attempt_id=first_bind_attempt_id,
            )
            session.add(row)
            session.flush()
        else:
            if variant_id is not None:
                row.variant_id = int(variant_id)
            if handle:
                row.venmo_handle = handle
            row.bound_via = bound_via
            row.bound_at = now
            if bound_by_telegram_user_id is not None:
                row.bound_by_telegram_user_id = bound_by_telegram_user_id
            if first_bind_attempt_id is not None:
                row.first_bind_attempt_id = first_bind_attempt_id

        binding_id = int(row.id)

    logger.info(
        "group_method_binding recorded id=%s chat_id=%s slug=%s via=%s variant_id=%s",
        binding_id,
        telegram_chat_id,
        slug,
        bound_via,
        variant_id,
    )
    return binding_id


def complete_attempt_from_payment(
    *,
    attempt_id: int,
    venmo_payment_id: int,
) -> Optional[BindAttemptInfo]:
    now = datetime.now(timezone.utc)
    with get_db() as session:
        attempt = (
            session.query(PaymentMethodBindAttempt)
            .filter_by(id=int(attempt_id), status=ATTEMPT_STATUS_PENDING)
            .one_or_none()
        )
        if attempt is None:
            return None
        if attempt.expires_at < now:
            attempt.status = ATTEMPT_STATUS_EXPIRED
            attempt.completed_at = now
            return None

        attempt.status = ATTEMPT_STATUS_SUCCEEDED
        attempt.venmo_payment_id = int(venmo_payment_id)
        attempt.completed_at = now

        info = BindAttemptInfo(
            id=int(attempt.id),
            telegram_chat_id=int(attempt.telegram_chat_id),
            club_id=int(attempt.club_id),
            variant_id=int(attempt.variant_id),
            amount_cents=int(attempt.amount_cents),
            expires_at=attempt.expires_at,
        )

    logger.info(
        "bind_attempt succeeded id=%s payment_id=%s chat_id=%s",
        attempt_id,
        venmo_payment_id,
        info.telegram_chat_id,
    )
    return info


def match_pending_venmo_setup_in_session(
    session,
    *,
    amount_cents: int,
    venmo_handle: str,
) -> Optional[PaymentMethodBindAttempt]:
    """Return a pending bind attempt matching ingest amount + variant handle."""
    handle = _normalize_venmo_handle(venmo_handle)
    if not handle:
        return None
    now = datetime.now(timezone.utc)
    _expire_stale_pending_global(session, now)
    candidates = (
        session.query(PaymentMethodBindAttempt)
        .filter_by(
            payment_method_slug="venmo",
            status=ATTEMPT_STATUS_PENDING,
            amount_cents=int(amount_cents),
        )
        .filter(PaymentMethodBindAttempt.expires_at >= now)
        .order_by(PaymentMethodBindAttempt.created_at)
        .all()
    )
    for attempt in candidates:
        variant = session.query(ClubPaymentTierVariant).get(int(attempt.variant_id))
        if not variant:
            continue
        variant_handle = extract_venmo_handle_from_text(variant.response_text)
        if not variant_handle:
            variant_handle = extract_venmo_handle_from_text(variant.response_caption)
        if variant_handle and variant_handle == handle:
            return attempt
    return None


def complete_attempt_in_session(
    session,
    attempt: PaymentMethodBindAttempt,
    *,
    venmo_payment_id: int,
) -> bool:
    now = datetime.now(timezone.utc)
    if attempt.status != ATTEMPT_STATUS_PENDING or attempt.expires_at < now:
        if attempt.status == ATTEMPT_STATUS_PENDING:
            attempt.status = ATTEMPT_STATUS_EXPIRED
            attempt.completed_at = now
        return False
    attempt.status = ATTEMPT_STATUS_SUCCEEDED
    attempt.venmo_payment_id = int(venmo_payment_id)
    attempt.completed_at = now
    return True


def record_group_binding_in_session(
    session,
    *,
    telegram_chat_id: int,
    club_id: int,
    payment_method_slug: str,
    bound_via: str,
    variant_id: int | None = None,
    venmo_handle: str | None = None,
    bound_by_telegram_user_id: int | None = None,
    first_bind_attempt_id: int | None = None,
) -> int:
    slug = (payment_method_slug or "").strip().lower()
    handle = _normalize_venmo_handle(venmo_handle) if venmo_handle else None
    now = datetime.now(timezone.utc)
    row = (
        session.query(GroupPaymentMethodBinding)
        .filter_by(
            telegram_chat_id=int(telegram_chat_id),
            payment_method_slug=slug,
        )
        .one_or_none()
    )
    if row is None:
        row = GroupPaymentMethodBinding(
            telegram_chat_id=int(telegram_chat_id),
            club_id=int(club_id),
            payment_method_slug=slug,
            variant_id=int(variant_id) if variant_id else None,
            venmo_handle=handle,
            bound_via=bound_via,
            bound_at=now,
            bound_by_telegram_user_id=bound_by_telegram_user_id,
            first_bind_attempt_id=first_bind_attempt_id,
        )
        session.add(row)
        session.flush()
    else:
        if variant_id is not None:
            row.variant_id = int(variant_id)
        if handle:
            row.venmo_handle = handle
        row.bound_via = bound_via
        row.bound_at = now
        if bound_by_telegram_user_id is not None:
            row.bound_by_telegram_user_id = bound_by_telegram_user_id
        if first_bind_attempt_id is not None:
            row.first_bind_attempt_id = first_bind_attempt_id
    return int(row.id)


def cancel_pending_attempts_for_chat_in_session(
    session,
    *,
    telegram_chat_id: int,
    payment_method_slug: str,
) -> int:
    return cancel_pending_attempts_for_chat(
        session,
        telegram_chat_id=int(telegram_chat_id),
        payment_method_slug=payment_method_slug,
    )


def _expire_stale_pending_global(session, now: datetime) -> None:
    (
        session.query(PaymentMethodBindAttempt)
        .filter_by(status=ATTEMPT_STATUS_PENDING)
        .filter(PaymentMethodBindAttempt.expires_at < now)
        .update(
            {PaymentMethodBindAttempt.status: ATTEMPT_STATUS_EXPIRED},
            synchronize_session=False,
        )
    )


def resolve_effective_min_cents_for_method(
    method_id: int,
    *,
    deposit_amount: Decimal | None = None,
) -> tuple[int, int | None]:
    """Return (effective_min_cents, tier_id) for setup amount allocation."""
    from bot.services.club import get_tier_for_amount

    with get_db() as session:
        method = session.query(ClubPaymentMethod).get(int(method_id))
        if not method:
            raise ValueError(f"Payment method {method_id} not found")
        method_min = method.min_amount
        tier_id: int | None = None
        tier_min: Decimal | None = None
        if deposit_amount is not None:
            tier = get_tier_for_amount(int(method_id), deposit_amount)
            if tier:
                tier_id = int(tier["id"])
                tier_min = tier.get("min_amount")
        cents = effective_min_cents(method_min=method_min, tier_min=tier_min)
        return cents, tier_id


def format_first_time_venmo_setup_message(
    *,
    setup_amount_cents: int,
    min_display_cents: int,
    variant_response_text: str | None,
) -> str:
    setup_display = _format_amount_display(int(setup_amount_cents))
    min_display = _format_amount_display(int(min_display_cents))
    url = extract_venmo_url(variant_response_text) or "—"
    venmo_line = f"Venmo: {url}"

    return (
        "FIRST-TIME VENMO SETUP:\n\n"
        f"To link your payment method to this group chat, please send EXACTLY "
        f"{setup_display} to the VENMO info below.\n\n"
        f"IMPORTANT: Do NOT round the amount to {min_display}. The exact amount helps "
        f"us match your payment to this chat faster. This is a one-time setup step for "
        f"this payment method. Future deposits can be sent normally once your method is "
        f"linked.\n\n"
        f"{venmo_line}\n\n"
        "After sending, please post a screenshot here. An agent will confirm the "
        "transaction and add your chips as soon as it comes through."
    )


def infer_variant_id_for_venmo_handle(
    club_id: int,
    venmo_handle: str,
) -> Optional[int]:
    """Match handle to a club Venmo variant response text."""
    handle = _normalize_venmo_handle(venmo_handle)
    if not handle:
        return None
    needle = handle.lstrip("@").lower()

    with get_db() as session:
        method = (
            session.query(ClubPaymentMethod)
            .filter_by(club_id=int(club_id), direction="deposit", slug="venmo")
            .one_or_none()
        )
        if not method:
            return None
        variants = (
            session.query(ClubPaymentTierVariant)
            .filter_by(method_id=int(method.id))
            .all()
        )
        for v in variants:
            for field in (v.response_text, v.response_caption):
                h = extract_venmo_handle_from_text(field)
                if h and h.lstrip("@").lower() == needle:
                    return int(v.id)
    return None
