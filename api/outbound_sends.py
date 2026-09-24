"""Ingest and list outbound Venmo and Cash App sends."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.method_owner import normalize_method_owner
from bot.services.cashapp_variant_fields import normalize_cashapp_tag
from bot.services.venmo_payments import parse_amount_cents
from bot.services.venmo_variant_fields import normalize_venmo_tag
from db.models import ClubPaymentMethod, ClubPaymentTierVariant, OutboundSend

METHODS = frozenset({"venmo", "cashapp"})
NO_MATCHING_TAG = "no matching tag found"


@dataclass(frozen=True)
class OutboundSendResult:
    id: int
    created: bool
    tag_matched: bool
    warning: str | None


def normalize_method(value: str) -> str:
    method = (value or "").strip().lower()
    if method not in METHODS:
        allowed = ", ".join(sorted(METHODS))
        raise ValueError(f"method must be one of: {allowed}")
    return method


def normalize_tag(method: str, raw: str) -> str:
    if method == "venmo":
        tag = normalize_venmo_tag(raw)
    else:
        tag = normalize_cashapp_tag(raw)
    if not tag or tag in {"@", "$"}:
        raise ValueError("tag is required")
    return tag


def normalize_recipient(value: str) -> str:
    recipient = " ".join((value or "").split())
    if not recipient:
        raise ValueError("recipient is required")
    return recipient


def normalize_source_external_id(value: str) -> str:
    source_id = (value or "").strip()
    if not source_id:
        raise ValueError("source_external_id is required")
    return source_id


def parse_positive_amount_cents(amount: str | int | float) -> int:
    cents = parse_amount_cents(amount)
    if cents <= 0:
        raise ValueError("amount must be greater than zero")
    return cents


def tag_matches_deposit_variant(db: Session, *, method: str, tag: str) -> bool:
    column = (
        ClubPaymentTierVariant.venmo_tag
        if method == "venmo"
        else ClubPaymentTierVariant.cashapp_tag
    )
    normalizer = normalize_venmo_tag if method == "venmo" else normalize_cashapp_tag
    rows = (
        db.query(column)
        .join(
            ClubPaymentMethod,
            ClubPaymentMethod.id == ClubPaymentTierVariant.method_id,
        )
        .filter(
            ClubPaymentMethod.direction == "deposit",
            ClubPaymentMethod.slug == method,
            column.isnot(None),
        )
        .all()
    )
    return any(normalizer(row[0]) == tag for row in rows)


def _prepared_fields(
    *,
    method: str,
    tag: str,
    method_owner: str,
    recipient: str,
    amount: str | int | float,
    source_external_id: str,
    paid_at: str | None,
) -> dict:
    method_slug = normalize_method(method)
    stored_tag = normalize_tag(method_slug, tag)
    return {
        "method": method_slug,
        "tag": stored_tag,
        "method_owner": normalize_method_owner(method_owner),
        "recipient": normalize_recipient(recipient),
        "amount_cents": parse_positive_amount_cents(amount),
        "source_external_id": normalize_source_external_id(source_external_id),
        "paid_at": (paid_at or "").strip() or None,
    }


def _flush_or_duplicate(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ValueError("source_external_id already exists") from None


def _reject_duplicate_source_id(
    db: Session, source_id: str, *, exclude_id: int | None = None
) -> None:
    query = db.query(OutboundSend).filter(OutboundSend.source_external_id == source_id)
    if exclude_id is not None:
        query = query.filter(OutboundSend.id != exclude_id)
    if query.one_or_none() is not None:
        raise ValueError("source_external_id already exists")


def create_outbound_send(
    db: Session,
    *,
    method: str,
    tag: str,
    method_owner: str,
    recipient: str,
    amount: str | int | float,
    source_external_id: str,
    paid_at: str | None = None,
) -> OutboundSend:
    fields = _prepared_fields(
        method=method,
        tag=tag,
        method_owner=method_owner,
        recipient=recipient,
        amount=amount,
        source_external_id=source_external_id,
        paid_at=paid_at,
    )
    _reject_duplicate_source_id(db, fields["source_external_id"])
    matched = tag_matches_deposit_variant(
        db, method=fields["method"], tag=fields["tag"]
    )
    row = OutboundSend(tag_matched=matched, **fields)
    db.add(row)
    _flush_or_duplicate(db)
    return row


def update_outbound_send(
    db: Session,
    send_id: int,
    *,
    method: str,
    tag: str,
    method_owner: str,
    recipient: str,
    amount: str | int | float,
    source_external_id: str,
    paid_at: str | None = None,
) -> OutboundSend:
    row = db.get(OutboundSend, send_id)
    if row is None:
        raise LookupError("outbound send not found")
    fields = _prepared_fields(
        method=method,
        tag=tag,
        method_owner=method_owner,
        recipient=recipient,
        amount=amount,
        source_external_id=source_external_id,
        paid_at=paid_at,
    )
    _reject_duplicate_source_id(db, fields["source_external_id"], exclude_id=send_id)
    matched = tag_matches_deposit_variant(
        db, method=fields["method"], tag=fields["tag"]
    )
    for key, value in fields.items():
        setattr(row, key, value)
    row.tag_matched = matched
    _flush_or_duplicate(db)
    return row


def delete_outbound_send(db: Session, send_id: int) -> None:
    row = db.get(OutboundSend, send_id)
    if row is None:
        raise LookupError("outbound send not found")
    db.delete(row)
    db.flush()


def ingest_outbound_send(
    db: Session,
    *,
    method: str,
    tag: str,
    method_owner: str,
    recipient: str,
    amount: str | int | float,
    source_external_id: str,
    paid_at: str | None = None,
) -> OutboundSendResult:
    method_slug = normalize_method(method)
    stored_tag = normalize_tag(method_slug, tag)
    owner = normalize_method_owner(method_owner)
    who = normalize_recipient(recipient)
    amount_cents = parse_positive_amount_cents(amount)
    source_id = normalize_source_external_id(source_external_id)
    paid = (paid_at or "").strip() or None

    existing = (
        db.query(OutboundSend).filter_by(source_external_id=source_id).one_or_none()
    )
    if existing is not None:
        return _result(existing, created=False)

    matched = tag_matches_deposit_variant(db, method=method_slug, tag=stored_tag)
    row = OutboundSend(
        method=method_slug,
        tag=stored_tag,
        tag_matched=matched,
        method_owner=owner,
        recipient=who,
        amount_cents=amount_cents,
        source_external_id=source_id,
        paid_at=paid,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.query(OutboundSend).filter_by(source_external_id=source_id).one()
        return _result(existing, created=False)
    return _result(row, created=True)


def list_outbound_sends(
    db: Session,
    *,
    method: str | None = None,
    tag: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[OutboundSend], int]:
    query = db.query(OutboundSend)
    method_slug = None
    if method and method.strip():
        method_slug = normalize_method(method)
        query = query.filter(OutboundSend.method == method_slug)
    if tag and tag.strip():
        stored_tag = _filter_tag(method_slug, tag)
        if stored_tag is not None:
            query = query.filter(OutboundSend.tag == stored_tag)
    if from_dt is not None:
        query = query.filter(OutboundSend.created_at >= from_dt)
    if to_dt is not None:
        query = query.filter(OutboundSend.created_at <= to_dt)
    total = query.count()
    items = (
        query.order_by(OutboundSend.created_at.desc(), OutboundSend.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return items, total


def _filter_tag(method: str | None, raw: str) -> str | None:
    """Return a stored tag, or None when the filter is only a prefix."""
    if method == "cashapp":
        chosen = "cashapp"
    elif method == "venmo":
        chosen = "venmo"
    else:
        chosen = "cashapp" if raw.strip().startswith("$") else "venmo"
    try:
        return normalize_tag(chosen, raw)
    except ValueError:
        return None


def _result(row: OutboundSend, *, created: bool) -> OutboundSendResult:
    warning = None if row.tag_matched else NO_MATCHING_TAG
    return OutboundSendResult(
        id=int(row.id),
        created=created,
        tag_matched=bool(row.tag_matched),
        warning=warning,
    )
