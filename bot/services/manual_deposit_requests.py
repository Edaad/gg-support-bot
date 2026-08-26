"""Manual trade-request deposit rows: capacity + atomic create."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from db.connection import get_db
from db.models import ClubPaymentMethod, ManualDepositRequest

logger = logging.getLogger(__name__)


class ManualDepositCapacityError(Exception):
    """Raised when a request would exceed the method deposit limit."""


def sum_for_method(session: Session, method_id: int) -> Decimal:
    total = (
        session.query(func.coalesce(func.sum(ManualDepositRequest.amount), 0))
        .filter(ManualDepositRequest.method_id == int(method_id))
        .scalar()
    )
    return Decimal(str(total or 0))


def capacity_allows(
    session: Session,
    *,
    method_id: int,
    amount: Decimal,
    deposit_limit: Optional[Decimal] = None,
) -> bool:
    limit = deposit_limit
    if limit is None:
        method = session.query(ClubPaymentMethod).get(int(method_id))
        if not method or method.deposit_limit is None:
            return False
        limit = Decimal(str(method.deposit_limit))
    else:
        limit = Decimal(str(limit))
    if limit <= 0:
        return False
    current = sum_for_method(session, int(method_id))
    return current + Decimal(str(amount)) <= limit


def method_has_capacity_for_amount(
    method_id: int,
    amount: Optional[Decimal],
    *,
    deposit_limit: Optional[Decimal] = None,
) -> bool:
    """Used when listing methods for /deposit (amount may be None)."""
    with get_db() as session:
        method = session.query(ClubPaymentMethod).get(int(method_id))
        if not method or not bool(getattr(method, "tracks_manual_requests", False)):
            return True
        limit = (
            Decimal(str(deposit_limit))
            if deposit_limit is not None
            else (
                Decimal(str(method.deposit_limit))
                if method.deposit_limit is not None
                else None
            )
        )
        if limit is None or limit <= 0:
            return False
        current = sum_for_method(session, int(method_id))
        if amount is None:
            return current < limit
        return current + Decimal(str(amount)) <= limit


def create_request_atomic(
    *,
    club_id: int,
    method_id: int,
    amount: Decimal,
    telegram_chat_id: int,
    group_title: Optional[str] = None,
) -> ManualDepositRequest:
    """Lock method row, re-check capacity, insert request. Raises ManualDepositCapacityError."""
    amount_dec = Decimal(str(amount))
    if amount_dec <= 0:
        raise ValueError("Amount must be positive")

    with get_db() as session:
        method = (
            session.query(ClubPaymentMethod)
            .filter(ClubPaymentMethod.id == int(method_id))
            .with_for_update()
            .one_or_none()
        )
        if not method or not bool(getattr(method, "tracks_manual_requests", False)):
            raise ValueError("Method is not a manual trade-request method")
        if not method.is_active:
            raise ManualDepositCapacityError("This payment method is no longer available.")
        if method.deposit_limit is None:
            raise ManualDepositCapacityError("This payment method has no capacity limit set.")
        if not capacity_allows(
            session,
            method_id=int(method_id),
            amount=amount_dec,
            deposit_limit=Decimal(str(method.deposit_limit)),
        ):
            raise ManualDepositCapacityError(
                "This payment method is at capacity for that amount."
            )

        variant = (method.manual_request_variant_name or "").strip() or "default"
        row = ManualDepositRequest(
            club_id=int(club_id),
            method_id=int(method.id),
            method_name=method.name,
            method_slug=(method.slug or "").strip().lower(),
            variant_name=variant,
            group_title=(group_title or "").strip()[:512] or None,
            amount=amount_dec,
            telegram_chat_id=int(telegram_chat_id),
            trade_record_checked=False,
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        return row
