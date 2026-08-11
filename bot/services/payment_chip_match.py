"""Best-effort match of chip-adds (/add, auto-deposit) to payment notifications."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError

from db.connection import get_db
from db.models import (
    CashAppPayment,
    CryptoPayment,
    PayPalPayment,
    PaymentChipMatch,
    StripeCheckoutSession,
    VenmoPayment,
    ZellePayment,
)

logger = logging.getLogger(__name__)

LOOKBACK = timedelta(hours=2)
AMOUNT_TOLERANCE_CENTS = 100
TOP_UNMATCHED = 3

VIA_ADD = "add"
VIA_AUTO_DEPOSIT = "auto_deposit"


@dataclass(frozen=True)
class PaymentCandidate:
    method_slug: str
    payment_id: int
    amount_cents: int
    telegram_chat_id: int
    club_id: Optional[int]
    group_title: Optional[str]
    reference_at: datetime
    transaction_hash: Optional[str] = None


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_payment_reference_at(
    *,
    paid_at: str | None,
    created_at: datetime | None,
    bound_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> datetime | None:
    """Prefer paid_at (ISO), else completed_at/bound_at/created_at."""
    raw = (paid_at or "").strip()
    if raw:
        try:
            normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
            return _ensure_aware(datetime.fromisoformat(normalized))
        except ValueError:
            pass
    for dt in (completed_at, bound_at, created_at):
        if dt is not None:
            return _ensure_aware(dt)
    return None


def amount_within_tolerance(chip_amount_cents: int, payment_amount_cents: int) -> bool:
    return abs(int(chip_amount_cents) - int(payment_amount_cents)) <= AMOUNT_TOLERANCE_CENTS


def via_display(via: str) -> str:
    if via == VIA_AUTO_DEPOSIT:
        return "auto-deposit"
    return "/add"


def format_match_message(
    *,
    via: str,
    amount_cents: int,
    matched: PaymentCandidate | None,
    recent_unmatched: list[PaymentCandidate],
) -> str:
    from bot.services.venmo_payments import escape_notification_html, format_amount_display

    amount = format_amount_display(int(amount_cents), bold=False)
    via_label = via_display(via)
    if matched is not None:
        title = (matched.group_title or "").strip() or "(unknown group)"
        return (
            f"Payment match — {via_label} {amount}\n"
            f"→ {escape_notification_html(matched.method_slug)} "
            f"#{matched.payment_id} · {escape_notification_html(title)}"
        )

    lines = [
        f"Payment match — {via_label} {amount} · no match",
        "Recent unmatched:",
    ]
    if not recent_unmatched:
        lines.append("• (none for this group)")
    else:
        for cand in recent_unmatched:
            title = (cand.group_title or "").strip() or "(unknown group)"
            pay_amt = format_amount_display(int(cand.amount_cents), bold=False)
            lines.append(
                f"• {escape_notification_html(cand.method_slug)} {pay_amt} · "
                f"{escape_notification_html(title)} (#{cand.payment_id})"
            )
    return "\n".join(lines)


def _matched_keys(session) -> set[tuple[str, int]]:
    rows = session.query(
        PaymentChipMatch.payment_method_slug,
        PaymentChipMatch.payment_id,
    ).all()
    return {(str(slug), int(pid)) for slug, pid in rows}


def _load_candidates_for_chat(
    session,
    telegram_chat_id: int,
    *,
    now: datetime,
    require_in_window: bool,
) -> list[PaymentCandidate]:
    """Load bound payments for a GC that are not yet chip-matched."""
    matched = _matched_keys(session)
    window_start = now - LOOKBACK
    out: list[PaymentCandidate] = []

    def _maybe_add(
        *,
        method_slug: str,
        payment_id: int,
        amount_cents: int,
        club_id: int | None,
        group_title: str | None,
        reference_at: datetime | None,
        transaction_hash: str | None = None,
    ) -> None:
        if (method_slug, int(payment_id)) in matched:
            return
        if reference_at is None:
            return
        ref = _ensure_aware(reference_at)
        if require_in_window and ref < window_start:
            return
        out.append(
            PaymentCandidate(
                method_slug=method_slug,
                payment_id=int(payment_id),
                amount_cents=int(amount_cents),
                telegram_chat_id=int(telegram_chat_id),
                club_id=int(club_id) if club_id is not None else None,
                group_title=(group_title or "").strip() or None,
                reference_at=ref,
                transaction_hash=(transaction_hash or "").strip() or None,
            )
        )

    chat_id = int(telegram_chat_id)

    for row in (
        session.query(CryptoPayment)
        .filter(CryptoPayment.telegram_chat_id == chat_id)
        .all()
    ):
        _maybe_add(
            method_slug="crypto",
            payment_id=row.id,
            amount_cents=row.amount_cents,
            club_id=row.club_id,
            group_title=row.bound_group_title_at_bind,
            reference_at=parse_payment_reference_at(
                paid_at=row.paid_at,
                created_at=row.created_at,
                bound_at=row.bound_at,
            ),
            transaction_hash=row.transaction_hash,
        )

    for model, slug in (
        (VenmoPayment, "venmo"),
        (ZellePayment, "zelle"),
        (CashAppPayment, "cashapp"),
        (PayPalPayment, "paypal"),
    ):
        for row in session.query(model).filter(model.telegram_chat_id == chat_id).all():
            _maybe_add(
                method_slug=slug,
                payment_id=row.id,
                amount_cents=row.amount_cents,
                club_id=row.club_id,
                group_title=row.bound_group_title_at_bind,
                reference_at=parse_payment_reference_at(
                    paid_at=row.paid_at,
                    created_at=row.created_at,
                    bound_at=getattr(row, "bound_at", None),
                ),
            )

    for row in (
        session.query(StripeCheckoutSession)
        .filter(
            StripeCheckoutSession.telegram_chat_id == chat_id,
            StripeCheckoutSession.status == "completed",
        )
        .all()
    ):
        _maybe_add(
            method_slug="stripe",
            payment_id=row.id,
            amount_cents=row.amount_cents,
            club_id=row.club_id,
            group_title=None,
            reference_at=parse_payment_reference_at(
                paid_at=None,
                created_at=row.created_at,
                completed_at=row.completed_at,
            ),
        )

    return out


def pick_best_candidate(
    candidates: list[PaymentCandidate],
    *,
    amount_cents: int,
    prefer_method_slug: str | None = None,
    prefer_payment_id: int | None = None,
) -> PaymentCandidate | None:
    """Pick unmatched → closest amount (±$1) → newest; prefer explicit payment if eligible."""
    in_tol = [c for c in candidates if amount_within_tolerance(amount_cents, c.amount_cents)]
    if not in_tol:
        return None

    if prefer_method_slug and prefer_payment_id is not None:
        preferred = [
            c
            for c in in_tol
            if c.method_slug == prefer_method_slug
            and c.payment_id == int(prefer_payment_id)
        ]
        if preferred:
            return preferred[0]

    in_tol.sort(
        key=lambda c: (
            abs(int(c.amount_cents) - int(amount_cents)),
            -c.reference_at.timestamp(),
        )
    )
    return in_tol[0]


def top_unmatched_for_chat(
    candidates: list[PaymentCandidate],
    *,
    limit: int = TOP_UNMATCHED,
) -> list[PaymentCandidate]:
    ordered = sorted(candidates, key=lambda c: c.reference_at, reverse=True)
    return ordered[:limit]


def _persist_match(
    session,
    *,
    candidate: PaymentCandidate,
    amount_cents: int,
    via: str,
    actor_telegram_user_id: int | None,
) -> PaymentChipMatch | None:
    existing = (
        session.query(PaymentChipMatch)
        .filter_by(
            payment_method_slug=candidate.method_slug,
            payment_id=int(candidate.payment_id),
        )
        .one_or_none()
    )
    if existing is not None:
        return None

    metadata: dict[str, Any] | None = None
    if candidate.method_slug == "crypto" and candidate.transaction_hash:
        metadata = {"transaction_hash": candidate.transaction_hash}

    row = PaymentChipMatch(
        payment_method_slug=candidate.method_slug,
        payment_id=candidate.payment_id,
        telegram_chat_id=int(candidate.telegram_chat_id),
        club_id=candidate.club_id,
        amount_cents=int(amount_cents),
        via=(via or VIA_ADD)[:32],
        actor_telegram_user_id=actor_telegram_user_id,
        matched_at=datetime.now(timezone.utc),
        metadata_json=metadata,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        logger.info(
            "payment_chip_match: duplicate skip method=%s payment_id=%s",
            candidate.method_slug,
            candidate.payment_id,
        )
        return None
    return row


def match_chip_add_sync(
    *,
    telegram_chat_id: int,
    amount_cents: int,
    via: str,
    actor_telegram_user_id: int | None = None,
    club_id: int | None = None,
    prefer_method_slug: str | None = None,
    prefer_payment_id: int | None = None,
    now: datetime | None = None,
) -> tuple[PaymentCandidate | None, list[PaymentCandidate], str]:
    """
    Run match + optional persist. Returns (matched, recent_unmatched_for_miss, message).

    On miss, recent_unmatched is top 3 for the GC (no amount filter). On hit, that list
    is empty and a DB row is inserted.
    """
    now_utc = _ensure_aware(now or datetime.now(timezone.utc))
    with get_db() as session:
        windowed = _load_candidates_for_chat(
            session,
            int(telegram_chat_id),
            now=now_utc,
            require_in_window=True,
        )
        # Fill missing stripe titles for display from live GC title
        if any(c.method_slug == "stripe" and not c.group_title for c in windowed):
            from bot.services.venmo_payments import resolve_display_group_title

            live_title = resolve_display_group_title(int(telegram_chat_id))
            if live_title:
                windowed = [
                    PaymentCandidate(
                        method_slug=c.method_slug,
                        payment_id=c.payment_id,
                        amount_cents=c.amount_cents,
                        telegram_chat_id=c.telegram_chat_id,
                        club_id=c.club_id if c.club_id is not None else club_id,
                        group_title=c.group_title or live_title,
                        reference_at=c.reference_at,
                        transaction_hash=c.transaction_hash,
                    )
                    for c in windowed
                ]

        picked = pick_best_candidate(
            windowed,
            amount_cents=int(amount_cents),
            prefer_method_slug=prefer_method_slug,
            prefer_payment_id=prefer_payment_id,
        )
        if picked is None:
            all_unmatched = _load_candidates_for_chat(
                session,
                int(telegram_chat_id),
                now=now_utc,
                require_in_window=False,
            )
            if any(c.method_slug == "stripe" and not c.group_title for c in all_unmatched):
                from bot.services.venmo_payments import resolve_display_group_title

                live_title = resolve_display_group_title(int(telegram_chat_id))
                if live_title:
                    all_unmatched = [
                        PaymentCandidate(
                            method_slug=c.method_slug,
                            payment_id=c.payment_id,
                            amount_cents=c.amount_cents,
                            telegram_chat_id=c.telegram_chat_id,
                            club_id=c.club_id if c.club_id is not None else club_id,
                            group_title=c.group_title or live_title,
                            reference_at=c.reference_at,
                            transaction_hash=c.transaction_hash,
                        )
                        for c in all_unmatched
                    ]
            recent = top_unmatched_for_chat(all_unmatched)
            text = format_match_message(
                via=via,
                amount_cents=int(amount_cents),
                matched=None,
                recent_unmatched=recent,
            )
            return None, recent, text

        # Prefer caller's club_id if candidate lacks one
        if picked.club_id is None and club_id is not None:
            picked = PaymentCandidate(
                method_slug=picked.method_slug,
                payment_id=picked.payment_id,
                amount_cents=picked.amount_cents,
                telegram_chat_id=picked.telegram_chat_id,
                club_id=int(club_id),
                group_title=picked.group_title,
                reference_at=picked.reference_at,
                transaction_hash=picked.transaction_hash,
            )

        persisted = _persist_match(
            session,
            candidate=picked,
            amount_cents=int(amount_cents),
            via=via,
            actor_telegram_user_id=actor_telegram_user_id,
        )
        if persisted is None:
            # Race: treat as unmatched for messaging (another add won)
            all_unmatched = _load_candidates_for_chat(
                session,
                int(telegram_chat_id),
                now=now_utc,
                require_in_window=False,
            )
            recent = top_unmatched_for_chat(all_unmatched)
            text = format_match_message(
                via=via,
                amount_cents=int(amount_cents),
                matched=None,
                recent_unmatched=recent,
            )
            return None, recent, text

        text = format_match_message(
            via=via,
            amount_cents=int(amount_cents),
            matched=picked,
            recent_unmatched=[],
        )
        return picked, [], text


# Temporarily off — staff PAYMENT_NOTIFICATION_CHAT_ID was too noisy.
_CHIP_MATCH_STAFF_NOTIFY_ENABLED = False


async def notify_payment_chip_match(text: str) -> None:
    if not _CHIP_MATCH_STAFF_NOTIFY_ENABLED:
        logger.info("payment_chip_match: staff notify disabled; skipping Telegram post")
        return
    from bot.services.venmo_payments import send_telegram_notification

    await send_telegram_notification(text)


async def run_payment_chip_match(
    *,
    telegram_chat_id: int,
    amount_cents: int,
    via: str,
    actor_telegram_user_id: int | None = None,
    club_id: int | None = None,
    prefer_method_slug: str | None = None,
    prefer_payment_id: int | None = None,
) -> None:
    """Match + staff notif. Never raises to callers."""
    try:
        _matched, _recent, text = await asyncio.to_thread(
            match_chip_add_sync,
            telegram_chat_id=int(telegram_chat_id),
            amount_cents=int(amount_cents),
            via=via,
            actor_telegram_user_id=actor_telegram_user_id,
            club_id=club_id,
            prefer_method_slug=prefer_method_slug,
            prefer_payment_id=prefer_payment_id,
        )
        await notify_payment_chip_match(text)
        logger.info(
            "payment_chip_match: via=%s chat_id=%s amount_cents=%s matched=%s",
            via,
            telegram_chat_id,
            amount_cents,
            bool(_matched),
        )
    except Exception:
        logger.exception(
            "payment_chip_match: failed via=%s chat_id=%s amount_cents=%s",
            via,
            telegram_chat_id,
            amount_cents,
        )


def schedule_payment_chip_match(
    *,
    telegram_chat_id: int,
    amount_cents: int,
    via: str,
    actor_telegram_user_id: int | None = None,
    club_id: int | None = None,
    prefer_method_slug: str | None = None,
    prefer_payment_id: int | None = None,
    create_task: Any | None = None,
) -> None:
    """Schedule matcher on an event loop (fail-safe)."""
    coro = run_payment_chip_match(
        telegram_chat_id=int(telegram_chat_id),
        amount_cents=int(amount_cents),
        via=via,
        actor_telegram_user_id=actor_telegram_user_id,
        club_id=club_id,
        prefer_method_slug=prefer_method_slug,
        prefer_payment_id=prefer_payment_id,
    )
    try:
        if create_task is not None:
            create_task(coro, name=f"payment-chip-match-{telegram_chat_id}")
        else:
            asyncio.get_running_loop().create_task(
                coro,
                name=f"payment-chip-match-{telegram_chat_id}",
            )
    except Exception:
        logger.exception(
            "payment_chip_match: schedule failed chat_id=%s",
            telegram_chat_id,
        )


def amount_decimal_to_cents(amount: Decimal | int | float) -> int:
    from bot.services.payment_method_binding import deposit_amount_to_cents

    if isinstance(amount, Decimal):
        return deposit_amount_to_cents(amount)
    return int(
        (Decimal(str(amount)) * Decimal(100)).quantize(Decimal("1"))
    )
