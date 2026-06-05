"""Per-group-chat payment method binding (first-time setup + observability)."""

from __future__ import annotations

import html as html_module
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal, Optional

from sqlalchemy import func

from db.connection import get_db
from db.models import (
    Club,
    ClubPaymentMethod,
    ClubPaymentTier,
    ClubPaymentTierVariant,
    GroupPaymentMethodBinding,
    PaymentMethodBindAttempt,
    VenmoPayerBinding,
    VenmoPayment,
)

logger = logging.getLogger(__name__)

BIND_ATTEMPT_TTL_SECONDS = 600

BIND_KIND_SPECIAL_AMOUNT = "special_amount"
BIND_KIND_MEMO_EMOJI = "memo_emoji"

_BINDABLE_METHOD_SLUGS = frozenset({"venmo", "zelle"})

# Memo/caption setup codes (cycled per pending attempt on a variant).
SETUP_MEMO_CODE_POOL: tuple[str, ...] = (
    "JIGGITIES",
    "QUEENS",
    "RIVER",
    "FLUSH",
    "RAISE",
    "NUTS",
    "ALLIN",
    "ACES",
    "KINGS",
    "QUADS",
)

_ZELLE_EMAIL_RE = re.compile(
    r"Zelle\s+Email:\s*(\S+@\S+)",
    re.IGNORECASE,
)
_ZELLE_NAME_RE = re.compile(
    r"Zelle\s+Name:\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)


def _normalize_club_name_key(name: str | None) -> str:
    return " ".join((name or "").strip().lower().split())


def _resolve_club_id(
    *,
    club_id: int | None = None,
    club_name: str | None = None,
) -> int | None:
    if club_id is not None:
        return int(club_id)
    if not club_name:
        return None
    target_key = _normalize_club_name_key(club_name)
    if not target_key:
        return None
    with get_db() as session:
        for club in session.query(Club).all():
            if _normalize_club_name_key(club.name) == target_key:
                return int(club.id)
    return None


def bind_mode_for_method(
    slug: str,
    *,
    club_id: int | None = None,
    club_name: str | None = None,
) -> Literal["special_amount", "memo_emoji"] | None:
    """Per-method first-time bind mode from dashboard club_payment_methods config."""
    method_slug = (slug or "").strip().lower()
    if method_slug not in _BINDABLE_METHOD_SLUGS:
        return None
    resolved_club_id = _resolve_club_id(club_id=club_id, club_name=club_name)
    if resolved_club_id is None:
        return None
    with get_db() as session:
        row = (
            session.query(ClubPaymentMethod)
            .filter_by(
                club_id=int(resolved_club_id),
                direction="deposit",
                slug=method_slug,
            )
            .one_or_none()
        )
        if not row or not getattr(row, "first_time_linking_enabled", False):
            return None
        mode = (getattr(row, "first_time_bind_mode", None) or "").strip().lower()
        if mode == BIND_KIND_SPECIAL_AMOUNT:
            return BIND_KIND_SPECIAL_AMOUNT
        if mode == BIND_KIND_MEMO_EMOJI:
            return BIND_KIND_MEMO_EMOJI
    return None


BOUND_VIA_SPECIAL_AMOUNT = "special_amount"
BOUND_VIA_MEMO_EMOJI = "memo_emoji"
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
    bind_kind: str
    amount_cents: int | None
    setup_emoji: str | None
    expires_at: datetime


@dataclass(frozen=True)
class SetupMatchResult:
    attempt_id: int
    telegram_chat_id: int
    club_id: int
    variant_id: int
    group_title: str


@dataclass(frozen=True)
class ExistingVenmoLink:
    linked_chat_id: int
    via: Literal["payer_binding", "group_binding"]


def _normalize_payer_name(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


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


def deposit_amount_to_cents(amount: Decimal) -> int:
    """Convert the /deposit amount the player entered to integer cents."""
    return int((amount * 100).quantize(Decimal("1")))


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


def list_chat_method_bindings(
    telegram_chat_id: int,
) -> list[ChatMethodBinding]:
    with get_db() as session:
        rows = (
            session.query(GroupPaymentMethodBinding)
            .filter_by(telegram_chat_id=int(telegram_chat_id))
            .order_by(GroupPaymentMethodBinding.payment_method_slug)
            .all()
        )
        return [
            ChatMethodBinding(
                id=int(row.id),
                telegram_chat_id=int(row.telegram_chat_id),
                club_id=int(row.club_id),
                payment_method_slug=str(row.payment_method_slug),
                variant_id=int(row.variant_id) if row.variant_id else None,
                venmo_handle=row.venmo_handle,
                bound_via=str(row.bound_via),
            )
            for row in rows
        ]


def unbind_chat_from_method(
    telegram_chat_id: int,
    payment_method_slug: str,
) -> bool:
    """Remove a group's payment-method link and cancel pending setup for that slug."""
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
            return False
        session.delete(row)
        cancel_pending_attempts_for_chat(
            session,
            telegram_chat_id=int(telegram_chat_id),
            payment_method_slug=slug,
        )
    logger.info(
        "group_binding removed chat_id=%s slug=%s",
        telegram_chat_id,
        slug,
    )
    return True


def unbind_chat_from_all_methods(telegram_chat_id: int) -> tuple[int, int]:
    """Remove all payment-method links and cancel all pending setup attempts for a chat.

    Returns (bindings_removed, attempts_cancelled).
    """
    with get_db() as session:
        rows = (
            session.query(GroupPaymentMethodBinding)
            .filter_by(telegram_chat_id=int(telegram_chat_id))
            .all()
        )
        bindings_removed = len(rows)
        for row in rows:
            session.delete(row)
        attempts_cancelled = cancel_all_pending_attempts_for_chat(
            session,
            telegram_chat_id=int(telegram_chat_id),
        )
    if bindings_removed or attempts_cancelled:
        logger.info(
            "group_bindings cleared chat_id=%s bindings_removed=%s attempts_cancelled=%s",
            telegram_chat_id,
            bindings_removed,
            attempts_cancelled,
        )
    return bindings_removed, attempts_cancelled


def unbind_by_id(binding_id: int) -> bool:
    """Remove a binding row by primary key."""
    with get_db() as session:
        row = (
            session.query(GroupPaymentMethodBinding)
            .filter_by(id=int(binding_id))
            .one_or_none()
        )
        if row is None:
            return False
        chat_id = int(row.telegram_chat_id)
        slug = str(row.payment_method_slug)
        session.delete(row)
        cancel_pending_attempts_for_chat(
            session,
            telegram_chat_id=chat_id,
            payment_method_slug=slug,
        )
    logger.info("group_binding removed id=%s", binding_id)
    return True


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


def allocate_setup_memo_code(session, *, variant_id: int) -> str:
    """Cycle left-to-right through SETUP_MEMO_CODE_POOL by pending memo attempt count."""
    _expire_stale_pending_for_variant(session, variant_id)
    pending_count = (
        session.query(func.count(PaymentMethodBindAttempt.id))
        .filter_by(
            variant_id=int(variant_id),
            status=ATTEMPT_STATUS_PENDING,
            bind_kind=BIND_KIND_MEMO_EMOJI,
        )
        .scalar()
    )
    index = int(pending_count or 0)
    if index >= len(SETUP_MEMO_CODE_POOL):
        raise ValueError(
            "No available setup codes for this variant (too many pending setups)"
        )
    return SETUP_MEMO_CODE_POOL[index]


def allocate_setup_amount_cents(
    session,
    *,
    variant_id: int,
    deposit_amount_cents: int,
) -> int:
    """Assign one cent below chosen deposit amount, minus pending special_amount setups."""
    deposit_cents = int(deposit_amount_cents)
    if deposit_cents < 2:
        raise ValueError("Deposit amount is too small for setup binding")
    _expire_stale_pending_for_variant(session, variant_id)
    base_cents = deposit_cents - 1
    pending_count = (
        session.query(func.count(PaymentMethodBindAttempt.id))
        .filter_by(
            variant_id=int(variant_id),
            status=ATTEMPT_STATUS_PENDING,
            bind_kind=BIND_KIND_SPECIAL_AMOUNT,
        )
        .scalar()
    )
    n = int(pending_count or 0)
    amount_cents = base_cents - n
    if amount_cents < 1:
        raise ValueError("No available setup amounts for this variant (too many pending)")
    return amount_cents


def cancel_all_pending_attempts_for_chat(
    session,
    *,
    telegram_chat_id: int,
) -> int:
    now = datetime.now(timezone.utc)
    q = session.query(PaymentMethodBindAttempt).filter_by(
        telegram_chat_id=int(telegram_chat_id),
        status=ATTEMPT_STATUS_PENDING,
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
    bind_kind: str,
    deposit_amount_cents: int | None = None,
    initiated_by_telegram_user_id: int | None,
) -> BindAttemptInfo:
    slug = (payment_method_slug or "").strip().lower()
    kind = (bind_kind or BIND_KIND_SPECIAL_AMOUNT).strip().lower()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=BIND_ATTEMPT_TTL_SECONDS)

    with get_db() as session:
        cancel_pending_attempts_for_chat(
            session,
            telegram_chat_id=int(telegram_chat_id),
            payment_method_slug=slug,
        )
        amount_cents: int | None = None
        setup_emoji: str | None = None
        bound_via = BOUND_VIA_SPECIAL_AMOUNT

        if kind == BIND_KIND_MEMO_EMOJI:
            setup_emoji = allocate_setup_memo_code(session, variant_id=int(variant_id))
            bound_via = BOUND_VIA_MEMO_EMOJI
        else:
            if deposit_amount_cents is None:
                raise ValueError("deposit_amount_cents required for special_amount bind")
            amount_cents = allocate_setup_amount_cents(
                session,
                variant_id=int(variant_id),
                deposit_amount_cents=int(deposit_amount_cents),
            )

        attempt = PaymentMethodBindAttempt(
            telegram_chat_id=int(telegram_chat_id),
            club_id=int(club_id),
            payment_method_slug=slug,
            method_id=int(method_id),
            tier_id=int(tier_id) if tier_id else None,
            variant_id=int(variant_id),
            bind_kind=kind,
            amount_cents=amount_cents,
            setup_emoji=setup_emoji,
            status=ATTEMPT_STATUS_PENDING,
            bound_via=bound_via,
            initiated_by_telegram_user_id=initiated_by_telegram_user_id,
            expires_at=expires_at,
        )
        session.add(attempt)
        session.flush()
        attempt_id = int(attempt.id)

    logger.info(
        "bind_attempt started id=%s chat_id=%s variant_id=%s bind_kind=%s "
        "amount_cents=%s setup_emoji=%s expires_at=%s",
        attempt_id,
        telegram_chat_id,
        variant_id,
        kind,
        amount_cents,
        setup_emoji,
        expires_at.isoformat(),
    )
    return BindAttemptInfo(
        id=attempt_id,
        telegram_chat_id=int(telegram_chat_id),
        club_id=int(club_id),
        variant_id=int(variant_id),
        bind_kind=kind,
        amount_cents=amount_cents,
        setup_emoji=setup_emoji,
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
            bind_kind=str(attempt.bind_kind),
            amount_cents=int(attempt.amount_cents)
            if attempt.amount_cents is not None
            else None,
            setup_emoji=attempt.setup_emoji,
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
            bind_kind=BIND_KIND_SPECIAL_AMOUNT,
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


def _memo_contains_code(memo: str, code: str) -> bool:
    if not memo or not code:
        return False
    return code.strip().upper() in memo.strip().upper()


def _variant_venmo_handle_matches(session, variant_id: int, venmo_handle: str) -> bool:
    handle = _normalize_venmo_handle(venmo_handle)
    if not handle:
        return False
    variant = session.query(ClubPaymentTierVariant).get(int(variant_id))
    if not variant:
        return False
    variant_handle = extract_venmo_handle_from_text(variant.response_text)
    if not variant_handle:
        variant_handle = extract_venmo_handle_from_text(variant.response_caption)
    return bool(variant_handle and variant_handle == handle)


def match_pending_memo_setup_in_session(
    session,
    *,
    payment_method_slug: str,
    venmo_handle: str,
    memo: str | None,
) -> Optional[PaymentMethodBindAttempt]:
    """Match pending memo_emoji setup by setup code in memo + Venmo handle on variant."""
    if not (memo or "").strip():
        return None
    slug = (payment_method_slug or "").strip().lower()
    now = datetime.now(timezone.utc)
    _expire_stale_pending_global(session, now)
    candidates = (
        session.query(PaymentMethodBindAttempt)
        .filter_by(
            payment_method_slug=slug,
            bind_kind=BIND_KIND_MEMO_EMOJI,
            status=ATTEMPT_STATUS_PENDING,
        )
        .filter(PaymentMethodBindAttempt.expires_at >= now)
        .filter(PaymentMethodBindAttempt.setup_emoji.isnot(None))
        .order_by(PaymentMethodBindAttempt.created_at)
        .all()
    )
    memo_text = memo.strip()
    for attempt in candidates:
        required = attempt.setup_emoji
        if not required or not _memo_contains_code(memo_text, required):
            continue
        if slug == "venmo" and not _variant_venmo_handle_matches(
            session, int(attempt.variant_id), venmo_handle
        ):
            continue
        return attempt
    return None


def find_existing_venmo_link_for_setup(
    session,
    *,
    payer_name: str,
    setup_chat_id: int,
) -> Optional[ExistingVenmoLink]:
    """Return an existing Venmo link for payer or setup chat, if any."""
    normalized = _normalize_payer_name(payer_name)
    payer_row = (
        session.query(VenmoPayerBinding)
        .filter_by(payer_name_normalized=normalized)
        .one_or_none()
    )
    if payer_row is not None:
        return ExistingVenmoLink(
            linked_chat_id=int(payer_row.telegram_chat_id),
            via="payer_binding",
        )

    group_row = (
        session.query(GroupPaymentMethodBinding)
        .filter_by(
            telegram_chat_id=int(setup_chat_id),
            payment_method_slug="venmo",
        )
        .one_or_none()
    )
    if group_row is not None:
        return ExistingVenmoLink(
            linked_chat_id=int(setup_chat_id),
            via="group_binding",
        )
    return None


def get_last_bound_deposit_at(
    session,
    *,
    payer_name: str,
    telegram_chat_id: int,
    exclude_payment_id: int,
) -> Optional[datetime]:
    """Most recent bound VenmoPayment created_at for payer in chat, or None."""
    normalized = _normalize_payer_name(payer_name)
    rows = (
        session.query(VenmoPayment)
        .filter(
            VenmoPayment.telegram_chat_id == int(telegram_chat_id),
            VenmoPayment.id != int(exclude_payment_id),
        )
        .order_by(VenmoPayment.created_at.desc())
        .limit(50)
        .all()
    )
    for row in rows:
        if _normalize_payer_name(row.payer_name) == normalized:
            return row.created_at
    return None


def cancel_setup_attempt_in_session(
    session,
    attempt: PaymentMethodBindAttempt,
) -> bool:
    """Cancel a pending setup attempt without completing bind."""
    now = datetime.now(timezone.utc)
    if attempt.status != ATTEMPT_STATUS_PENDING:
        return False
    attempt.status = ATTEMPT_STATUS_CANCELLED
    attempt.completed_at = now
    return True


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
    """Return (effective_min_cents, tier_id) from method/tier mins (tier pick helper)."""
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


def extract_zelle_details(text: str | None) -> tuple[str | None, str | None]:
    """Return (email, name) from variant response text."""
    if not text:
        return None, None
    email_m = _ZELLE_EMAIL_RE.search(text)
    name_m = _ZELLE_NAME_RE.search(text)
    email = email_m.group(1).strip() if email_m else None
    name = name_m.group(1).strip() if name_m else None
    return email, name


def format_setup_memo_code_highlight(*, use_html: bool = True) -> str:
    """Instruction before a separate message that contains only the setup code."""
    if use_html:
        return (
            "<b>Copy and paste the code in the next message</b> "
            "into your payment memo/caption."
        )
    return "Copy and paste the code in the next message into your payment memo/caption."


def format_setup_memo_code_message(setup_code: str, *, use_html: bool = True) -> str:
    """Standalone copy-paste message for the required memo/caption code."""
    code = (setup_code or "").strip()
    if use_html:
        return f"<code>{html_module.escape(code)}</code>"
    return code


def format_first_time_memo_setup_message(
    *,
    payment_method_slug: str,
    variant_response_text: str | None,
    use_html: bool = True,
) -> str:
    """First-time setup copy for memo/caption code binding (Venmo or Zelle)."""
    slug = (payment_method_slug or "").strip().lower()
    body_middle = (
        "The exact code helps us match your payment to this chat faster. "
        "This is a one-time setup step for this payment method. Future deposits "
        "can be sent normally once your method is linked."
    )

    if slug == "zelle":
        email, name = extract_zelle_details(variant_response_text)
        email_line = email or "—"
        name_line = name or "—"
        if use_html:
            safe_email = html_module.escape(email_line)
            safe_name = html_module.escape(name_line)
            return (
                "<b>FIRST-TIME ZELLE SETUP</b>\n"
                "────────────────────\n\n"
                "<b>Copy and paste the code above</b> into the caption "
                "when you send to the Zelle info below.\n\n"
                "<b>Use this code exactly.</b>\n\n"
                f"{body_middle}\n\n"
                f"<b>ZELLE EMAIL:</b> <code>{safe_email}</code>\n"
                f"<b>Zelle Name:</b> {safe_name}\n\n"
                "After sending, please post a screenshot here. An agent will confirm "
                "the transaction and add your chips as soon as it comes through."
            )
        return (
            "FIRST-TIME ZELLE SETUP\n"
            "--------------------\n\n"
            "Copy and paste the code above into the caption "
            "when you send to the Zelle info below.\n\n"
            "Use this code exactly.\n\n"
            f"{body_middle}\n\n"
            f"ZELLE EMAIL: {email_line}\n"
            f"Zelle Name: {name_line}\n\n"
            "After sending, please post a screenshot here. An agent will confirm "
            "the transaction and add your chips as soon as it comes through."
        )

    url = extract_venmo_url(variant_response_text) or "—"
    caption_word = "caption"
    if use_html:
        safe_url = html_module.escape(url, quote=True)
        return (
            "<b>FIRST-TIME VENMO SETUP</b>\n"
            "────────────────────\n\n"
            f"<b>Copy and paste the code above</b> into the {caption_word} "
            "when you send to the Venmo info below.\n\n"
            "<b>Use this code exactly.</b>\n\n"
            f"{body_middle}\n\n"
            f'<b>Venmo:</b> <a href="{safe_url}">{safe_url}</a>\n\n'
            "After sending, please post a screenshot here. An agent will confirm "
            "the transaction and add your chips as soon as it comes through."
        )
    return (
        "FIRST-TIME VENMO SETUP\n"
        "--------------------\n\n"
        f"Copy and paste the code above into the {caption_word} "
        "when you send to the Venmo info below.\n\n"
        "Use this code exactly.\n\n"
        f"{body_middle}\n\n"
        f"Venmo: {url}\n\n"
        "After sending, please post a screenshot here. An agent will confirm "
        "the transaction and add your chips as soon as it comes through."
    )


def format_setup_amount_highlight(amount_cents: int, *, use_html: bool = True) -> str:
    """Short standalone message highlighting the exact setup amount."""
    display = _format_amount_display(int(amount_cents))
    if use_html:
        return (
            f"<b>Send exactly</b>\n<code>{html_module.escape(display)}</code>"
        )
    return f"Send exactly:\n  {display}"


def format_first_time_venmo_setup_message(
    *,
    setup_amount_cents: int,
    chosen_amount_cents: int,
    variant_response_text: str | None,
    use_html: bool = True,
) -> str:
    """Build first-time Venmo setup copy. Default is Telegram HTML (parse_mode=HTML)."""
    setup_display = _format_amount_display(int(setup_amount_cents))
    chosen_display = _format_amount_display(int(chosen_amount_cents))
    url = extract_venmo_url(variant_response_text) or "—"

    if use_html:
        safe_setup = html_module.escape(setup_display)
        safe_chosen = html_module.escape(chosen_display)
        safe_url = html_module.escape(url, quote=True)
        return (
            "<b>FIRST-TIME VENMO SETUP</b>\n"
            "────────────────────\n\n"
            "<b>Pay this exact amount only:</b>\n"
            f"<code>{safe_setup}</code>\n\n"
            f"<b>Please do not send {safe_chosen}</b> (no rounding).\n\n"
            "The exact amount helps us match your payment to this chat faster. "
            "This is a one-time setup step for this payment method. Future deposits "
            "can be sent normally once your method is linked.\n\n"
            f'<b>Venmo:</b> <a href="{safe_url}">{safe_url}</a>\n\n'
            "Post a screenshot when done. An agent will confirm and add your chips."
        )

    return (
        "FIRST-TIME VENMO SETUP\n"
        "--------------------\n\n"
        "PAY THIS EXACT AMOUNT ONLY:\n"
        f"  {setup_display}\n\n"
        f"Do NOT send {chosen_display} (no rounding).\n\n"
        "The exact amount helps us match your payment to this chat faster. "
        "This is a one-time setup step for this payment method. Future deposits "
        "can be sent normally once your method is linked.\n\n"
        f"Venmo: {url}\n\n"
        "Post a screenshot when done. An agent will confirm and add your chips."
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
