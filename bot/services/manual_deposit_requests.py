"""Manual trade-request deposit rows: capacity + atomic create."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from bot.services.union_method_types import (
    union_type_display_name,
    union_type_from_display_name,
    validate_union_method_type,
)
from db.connection import get_db
from db.models import ClubPaymentMethod, ClubPaymentMethodClub, Group, ManualDepositRequest

UnionDepositSlackVariant = Literal["first", "repeat_verified", "repeat_open"]
ManualDepositSource = Literal["bot", "dashboard"]

logger = logging.getLogger(__name__)


class ManualDepositCapacityError(Exception):
    """Raised when a request would exceed the method deposit limit."""


class ManualDepositValidationError(Exception):
    """Raised when dashboard create/update validation fails."""


def _union_method_type_display(method: ClubPaymentMethod) -> str:
    raw = getattr(method, "union_type", None)
    if raw:
        try:
            return union_type_display_name(validate_union_method_type(str(raw)))
        except ValueError:
            pass
    return (method.name or "").strip()


def _deposit_snapshot_fields(method: ClubPaymentMethod) -> tuple[str, str, str]:
    method_tag = (getattr(method, "method_tag", None) or "").strip() or "default"
    return (
        _union_method_type_display(method),
        (method.slug or "").strip().lower(),
        method_tag,
    )


def _method_union_type(method: ClubPaymentMethod) -> Optional[str]:
    raw = getattr(method, "union_type", None)
    if raw:
        try:
            return validate_union_method_type(str(raw))
        except ValueError:
            pass
    return union_type_from_display_name(method.name or "")


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


def capacity_allows_for_update(
    session: Session,
    *,
    method_id: int,
    new_amount: Decimal,
    exclude_request_id: int,
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
    row = session.get(ManualDepositRequest, int(exclude_request_id))
    old_amount = Decimal(str(row.amount)) if row else Decimal("0")
    adjusted = current - old_amount + Decimal(str(new_amount))
    return adjusted <= limit


def method_club_ids(session: Session, method_id: int) -> set[int]:
    rows = (
        session.query(ClubPaymentMethodClub.club_id)
        .filter(ClubPaymentMethodClub.method_id == int(method_id))
        .all()
    )
    return {int(r[0]) for r in rows}


def validate_manual_deposit_amount(method: ClubPaymentMethod, amount: Decimal) -> None:
    amount_dec = Decimal(str(amount))
    if amount_dec <= 0:
        raise ManualDepositValidationError("Amount must be positive.")
    if method.min_amount is not None and amount_dec < Decimal(str(method.min_amount)):
        raise ManualDepositValidationError(
            f"Amount is below the minimum (${Decimal(str(method.min_amount)):,.2f})."
        )
    if method.max_amount is not None and amount_dec > Decimal(str(method.max_amount)):
        raise ManualDepositValidationError(
            f"Amount is above the maximum (${Decimal(str(method.max_amount)):,.2f})."
        )


def validate_manual_deposit_created_at(created_at: datetime) -> None:
    dt = created_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    if dt > datetime.now(timezone.utc):
        raise ManualDepositValidationError("Created at cannot be in the future.")


def resolve_deposit_group(
    session: Session,
    *,
    method_id: int,
    telegram_chat_id: int,
) -> Group:
    allowed_clubs = method_club_ids(session, int(method_id))
    if not allowed_clubs:
        raise ManualDepositValidationError("Method has no club membership configured.")
    group = (
        session.query(Group)
        .filter(
            Group.chat_id == int(telegram_chat_id),
            Group.club_id.in_(allowed_clubs),
        )
        .one_or_none()
    )
    if not group:
        raise ManualDepositValidationError(
            "Group not found for this method's clubs."
        )
    return group


def create_dashboard_manual_deposit_request(
    *,
    method_id: int,
    amount: Decimal,
    telegram_chat_id: int,
    created_at: Optional[datetime] = None,
    trade_record_checked: bool = False,
) -> ManualDepositRequest:
    """Dashboard create: capacity + min/max; inactive methods allowed."""
    amount_dec = Decimal(str(amount))
    when = created_at or datetime.now(timezone.utc)
    validate_manual_deposit_created_at(when)

    with get_db() as session:
        method = (
            session.query(ClubPaymentMethod)
            .filter(ClubPaymentMethod.id == int(method_id))
            .with_for_update()
            .one_or_none()
        )
        if not method or not bool(getattr(method, "tracks_manual_requests", False)):
            raise ManualDepositValidationError("Method is not a manual trade-request method.")
        validate_manual_deposit_amount(method, amount_dec)
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

        group = resolve_deposit_group(
            session,
            method_id=int(method_id),
            telegram_chat_id=int(telegram_chat_id),
        )
        group_title = (group.name or "").strip()[:512] or None
        type_display, internal_id, method_tag = _deposit_snapshot_fields(method)
        row = ManualDepositRequest(
            club_id=int(group.club_id),
            method_id=int(method.id),
            method_name=type_display,
            method_slug=internal_id,
            variant_name=method_tag,
            group_title=group_title,
            amount=amount_dec,
            telegram_chat_id=int(telegram_chat_id),
            trade_record_checked=bool(trade_record_checked),
            source="dashboard",
            created_at=when.astimezone(timezone.utc)
            if when.tzinfo
            else when.replace(tzinfo=timezone.utc),
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        session.expunge(row)
        return row


def update_dashboard_manual_deposit_request(
    *,
    request_id: int,
    amount: Optional[Decimal] = None,
    telegram_chat_id: Optional[int] = None,
    created_at: Optional[datetime] = None,
    trade_record_checked: Optional[bool] = None,
) -> ManualDepositRequest:
    """Dashboard partial update with capacity re-check on amount change."""
    with get_db() as session:
        row = (
            session.query(ManualDepositRequest)
            .filter(ManualDepositRequest.id == int(request_id))
            .with_for_update()
            .one_or_none()
        )
        if not row:
            raise ManualDepositValidationError("Request not found.")
        if row.method_id is None:
            raise ManualDepositValidationError("Request has no linked method.")

        method = session.get(ClubPaymentMethod, int(row.method_id))
        if not method or not bool(getattr(method, "tracks_manual_requests", False)):
            raise ManualDepositValidationError("Method is not a manual trade-request method.")

        if amount is not None:
            amount_dec = Decimal(str(amount))
            validate_manual_deposit_amount(method, amount_dec)
            if method.deposit_limit is None:
                raise ManualDepositCapacityError(
                    "This payment method has no capacity limit set."
                )
            if not capacity_allows_for_update(
                session,
                method_id=int(row.method_id),
                new_amount=amount_dec,
                exclude_request_id=int(row.id),
                deposit_limit=Decimal(str(method.deposit_limit)),
            ):
                raise ManualDepositCapacityError(
                    "This payment method is at capacity for that amount."
                )
            row.amount = amount_dec

        if telegram_chat_id is not None:
            group = resolve_deposit_group(
                session,
                method_id=int(row.method_id),
                telegram_chat_id=int(telegram_chat_id),
            )
            row.telegram_chat_id = int(telegram_chat_id)
            row.club_id = int(group.club_id)
            row.group_title = (group.name or "").strip()[:512] or None

        if created_at is not None:
            validate_manual_deposit_created_at(created_at)
            dt = created_at
            row.created_at = (
                dt.astimezone(timezone.utc)
                if dt.tzinfo
                else dt.replace(tzinfo=timezone.utc)
            )

        if trade_record_checked is not None:
            was_checked = bool(row.trade_record_checked)
            row.trade_record_checked = bool(trade_record_checked)
            if trade_record_checked and not was_checked:
                from bot.services.union_instruction_expiry import (
                    cancel_union_instruction_expiry,
                )

                cancel_union_instruction_expiry(int(row.id))

        session.flush()
        session.refresh(row)
        session.expunge(row)
        return row


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


def union_deposit_slack_variant(
    telegram_chat_id: int,
    *,
    union_type_slug: str,
) -> UnionDepositSlackVariant:
    """Classify Slack copy for a union manual deposit (call before insert)."""
    type_slug = (union_type_slug or "").strip().lower()
    with get_db() as session:
        rows = (
            session.query(
                ManualDepositRequest.method_name,
                ManualDepositRequest.trade_record_checked,
            )
            .filter(ManualDepositRequest.telegram_chat_id == int(telegram_chat_id))
            .all()
        )
    matching = [
        row
        for row in rows
        if union_type_from_display_name(row[0] or "") == type_slug
    ]
    if not matching:
        return "first"
    if any(bool(row[1]) for row in matching):
        return "repeat_verified"
    return "repeat_open"


def create_request_atomic(
    *,
    club_id: int,
    method_id: int,
    amount: Decimal,
    telegram_chat_id: int,
    group_title: Optional[str] = None,
    instruction_message_ids: Optional[list[int]] = None,
) -> ManualDepositRequest:
    """Lock method row, re-check capacity, insert request. Raises ManualDepositCapacityError."""
    from bot.services.union_instruction_expiry import instruction_expires_at_from_now
    from bot.services.union_method_types import union_type_from_display_name

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

        type_display, internal_id, method_tag = _deposit_snapshot_fields(method)
        row = ManualDepositRequest(
            club_id=int(club_id),
            method_id=int(method.id),
            method_name=type_display,
            method_slug=internal_id,
            variant_name=method_tag,
            group_title=(group_title or "").strip()[:512] or None,
            amount=amount_dec,
            telegram_chat_id=int(telegram_chat_id),
            trade_record_checked=False,
            source="bot",
        )
        if instruction_message_ids and _method_union_type(method):
            row.instruction_telegram_message_ids = [
                int(mid) for mid in instruction_message_ids if mid
            ]
            if row.instruction_telegram_message_ids:
                row.instruction_expires_at = instruction_expires_at_from_now()
        session.add(row)
        session.flush()
        session.refresh(row)
        session.expunge(row)
        return row
