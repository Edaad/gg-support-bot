"""Persist e2e auto-deposit outcomes for dashboard analytics."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from bot.services.club import get_auto_deposit_on_payment_enabled
from bot.services.player_details import gg_player_id_from_title
from db.connection import get_db
from db.models import (
    CashAppPayment,
    CryptoPayment,
    PayPalPayment,
    PaymentAutoDepositEvent,
    StripeCheckoutSession,
    VenmoPayment,
    ZellePayment,
)

logger = logging.getLogger(__name__)


def _normalize_group_title(group_title: object | None) -> str | None:
    if not isinstance(group_title, str):
        return None
    cleaned = group_title.strip()
    return cleaned or None


_PAYMENT_MODELS = {
    "venmo": VenmoPayment,
    "zelle": ZellePayment,
    "cashapp": CashAppPayment,
    "paypal": PayPalPayment,
    "crypto": CryptoPayment,
    "stripe": StripeCheckoutSession,
}


def _payment_at_for(
    payment_method_slug: str, payment_id: int
) -> datetime | None:
    model = _PAYMENT_MODELS.get(payment_method_slug)
    if model is None:
        return None
    with get_db() as session:
        row = session.query(model).filter_by(id=int(payment_id)).one_or_none()
        if row is None:
            return None
        ts = getattr(row, "created_at", None) or getattr(row, "bound_at", None)
        if ts is None:
            return None
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts


def record_auto_deposit_event(
    *,
    payment_method_slug: str,
    payment_id: int,
    club_id: int | None,
    telegram_chat_id: int | None,
    amount_cents: int,
    auto_bound: bool,
    goods_or_services: bool = False,
    group_title: str | None = None,
    status: str,
    skip_reason: str | None = None,
    chip_add_status: str | None = None,
    payment_at: datetime | None = None,
) -> None:
    """Upsert one auto-deposit analytics row (idempotent per payment)."""
    title = _normalize_group_title(group_title)
    gg_player_id = gg_player_id_from_title(title) if title else None
    club_enabled = False
    if club_id is not None:
        try:
            club_enabled = bool(get_auto_deposit_on_payment_enabled(int(club_id)))
        except Exception:
            logger.debug(
                "record_auto_deposit_event: could not read club toggle club_id=%s",
                club_id,
                exc_info=True,
            )
    occurred_at = payment_at
    if occurred_at is None:
        occurred_at = _payment_at_for(payment_method_slug, payment_id)
    if occurred_at is None:
        occurred_at = datetime.now(timezone.utc)
    elif occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)

    try:
        with get_db() as session:
            row = (
                session.query(PaymentAutoDepositEvent)
                .filter_by(
                    payment_method_slug=payment_method_slug,
                    payment_id=int(payment_id),
                )
                .one_or_none()
            )
            if row is None:
                row = PaymentAutoDepositEvent(
                    payment_method_slug=payment_method_slug,
                    payment_id=int(payment_id),
                )
                session.add(row)
            row.club_id = club_id
            row.telegram_chat_id = telegram_chat_id
            row.amount_cents = int(amount_cents)
            row.auto_bound = bool(auto_bound)
            row.goods_or_services = bool(goods_or_services)
            row.group_title = title
            row.gg_player_id = gg_player_id
            row.club_auto_deposit_enabled = club_enabled
            row.status = status
            row.skip_reason = skip_reason
            row.chip_add_status = chip_add_status
            row.payment_at = occurred_at
    except Exception:
        logger.exception(
            "record_auto_deposit_event failed method=%s payment_id=%s",
            payment_method_slug,
            payment_id,
        )
