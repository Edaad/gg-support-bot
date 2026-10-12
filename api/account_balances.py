"""Round Table Venmo and Cash App balance cards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from bot.services.cashapp_variant_fields import normalize_cashapp_tag
from bot.services.deposit_method_alerts import eastern_week_bounds_utc
from bot.services.payment_chip_match import parse_payment_reference_at
from bot.services.round_table_unions import ROUND_TABLE_CLUB_NAME
from bot.services.venmo_variant_fields import normalize_venmo_tag
from db.models import (
    AccountBalanceSettings,
    CashAppPayment,
    Club,
    ClubPaymentMethod,
    ClubPaymentTierVariant,
    OutboundSend,
    VenmoPayment,
)

METHODS = ("venmo", "cashapp")
METHOD_OWNER = "round-table"
EXCLUDED_HANDLES = frozenset({"janseashells", "creatorclub"})
WINDOW = timedelta(hours=168)
DEFAULT_OFFSET_CENTS = 0
DEFAULT_INFLOW_MAX_CENTS = 500_000
DEFAULT_SENDING_CEILING_CENTS = {"venmo": 1_000_000, "cashapp": 500_000}


@dataclass(frozen=True)
class BlockingSend:
    id: int
    amount_cents: int
    recipient: str
    occurred_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class AccountBalanceCard:
    method: str
    tag: str
    balance_cents: int
    offset_cents: int
    received_week_cents: int
    inflow_max_cents: int
    sending_remaining_cents: int
    sending_ceiling_cents: int
    send_power_maxed: bool
    inflow_maxed: bool


@dataclass
class _Money:
    amount_cents: int
    occurred_at: datetime | None


@dataclass
class _Send:
    id: int
    amount_cents: int
    recipient: str
    occurred_at: datetime | None


def normalize_method(value: str) -> str:
    method = (value or "").strip().lower()
    if method not in METHODS:
        allowed = ", ".join(METHODS)
        raise ValueError(f"method must be one of: {allowed}")
    return method


def normalize_tag(method: str, raw: str | None) -> str:
    if method == "venmo":
        return normalize_venmo_tag(raw)
    return normalize_cashapp_tag(raw)


def _handle_key(tag: str) -> str:
    return tag.lower().lstrip("@$")


def _excluded(tag: str) -> bool:
    return _handle_key(tag) in EXCLUDED_HANDLES


def _variant_tag(method: str, variant: ClubPaymentTierVariant) -> str:
    raw = variant.tag
    if not raw:
        raw = variant.venmo_tag if method == "venmo" else variant.cashapp_tag
    return normalize_tag(method, raw)


def _event_at(paid_at: str | None, created_at: datetime | None) -> datetime | None:
    return parse_payment_reference_at(paid_at=paid_at, created_at=created_at)


def _aware(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _cards_for_club(db: Session) -> dict[tuple[str, str], None]:
    rows = (
        db.query(ClubPaymentMethod.slug, ClubPaymentTierVariant)
        .join(
            ClubPaymentTierVariant,
            ClubPaymentTierVariant.method_id == ClubPaymentMethod.id,
        )
        .join(Club, Club.id == ClubPaymentMethod.club_id)
        .filter(Club.name.ilike(ROUND_TABLE_CLUB_NAME))
        .filter(ClubPaymentMethod.slug.in_(METHODS))
        .all()
    )
    found: dict[tuple[str, str], None] = {}
    for slug, variant in rows:
        method = (slug or "").strip().lower()
        if method not in METHODS:
            continue
        tag = _variant_tag(method, variant)
        if not tag or tag in {"@", "$"} or _excluded(tag):
            continue
        found[(method, tag)] = None
    return found


def _settings_by_key(db: Session) -> dict[tuple[str, str], AccountBalanceSettings]:
    rows = db.query(AccountBalanceSettings).all()
    return {(row.method, row.tag): row for row in rows}


def _limits(method: str, row: AccountBalanceSettings | None) -> tuple[int, int, int]:
    if row is None:
        return (
            DEFAULT_OFFSET_CENTS,
            DEFAULT_SENDING_CEILING_CENTS[method],
            DEFAULT_INFLOW_MAX_CENTS,
        )
    return row.offset_cents, row.sending_ceiling_cents, row.inflow_max_cents


def _load_payments(db: Session) -> dict[tuple[str, str], list[_Money]]:
    grouped: dict[tuple[str, str], list[_Money]] = {}
    venmo_rows = (
        db.query(
            VenmoPayment.venmo_handle,
            VenmoPayment.amount_cents,
            VenmoPayment.paid_at,
            VenmoPayment.created_at,
        )
        .filter(
            VenmoPayment.method_owner == METHOD_OWNER,
            VenmoPayment.is_test.is_(False),
        )
        .all()
    )
    for handle, amount_cents, paid_at, created_at in venmo_rows:
        tag = normalize_tag("venmo", handle)
        if not tag:
            continue
        grouped.setdefault(("venmo", tag), []).append(
            _Money(int(amount_cents), _event_at(paid_at, created_at))
        )
    cashapp_rows = (
        db.query(
            CashAppPayment.cashapp_handle,
            CashAppPayment.amount_cents,
            CashAppPayment.paid_at,
            CashAppPayment.created_at,
        )
        .filter(
            CashAppPayment.method_owner == METHOD_OWNER,
            CashAppPayment.is_test.is_(False),
        )
        .all()
    )
    for handle, amount_cents, paid_at, created_at in cashapp_rows:
        tag = normalize_tag("cashapp", handle)
        if not tag:
            continue
        grouped.setdefault(("cashapp", tag), []).append(
            _Money(int(amount_cents), _event_at(paid_at, created_at))
        )
    return grouped


def _load_sends(db: Session) -> dict[tuple[str, str], list[_Send]]:
    rows = (
        db.query(OutboundSend)
        .filter(
            OutboundSend.method.in_(METHODS),
            OutboundSend.method_owner == METHOD_OWNER,
        )
        .all()
    )
    grouped: dict[tuple[str, str], list[_Send]] = {}
    for row in rows:
        tag = normalize_tag(row.method, row.tag)
        if not tag:
            continue
        grouped.setdefault((row.method, tag), []).append(
            _Send(
                id=int(row.id),
                amount_cents=int(row.amount_cents),
                recipient=row.recipient,
                occurred_at=_event_at(row.paid_at, row.created_at),
            )
        )
    return grouped


def _in_week(occurred_at: datetime | None, start, end) -> bool:
    return occurred_at is not None and start <= occurred_at <= end


def _blocking(send: _Send, now: datetime) -> bool:
    if send.occurred_at is None:
        return False
    return send.occurred_at + WINDOW > now


def _card(
    method: str,
    tag: str,
    *,
    settings: AccountBalanceSettings | None,
    payments: list[_Money],
    sends: list[_Send],
    week_start,
    week_end,
    now: datetime,
) -> AccountBalanceCard:
    offset_cents, ceiling_cents, inflow_max_cents = _limits(method, settings)
    received_all = sum(row.amount_cents for row in payments)
    received_week = sum(
        row.amount_cents
        for row in payments
        if _in_week(row.occurred_at, week_start, week_end)
    )
    sent_all = sum(row.amount_cents for row in sends)
    sent_window = sum(row.amount_cents for row in sends if _blocking(row, now))
    remaining = ceiling_cents - sent_window
    return AccountBalanceCard(
        method=method,
        tag=tag,
        balance_cents=offset_cents + received_all - sent_all,
        offset_cents=offset_cents,
        received_week_cents=received_week,
        inflow_max_cents=inflow_max_cents,
        sending_remaining_cents=remaining,
        sending_ceiling_cents=ceiling_cents,
        send_power_maxed=remaining <= 0,
        inflow_maxed=received_week >= inflow_max_cents,
    )


def list_account_balances(
    db: Session, *, now: datetime | None = None
) -> dict[str, list[AccountBalanceCard]]:
    moment = _aware(now)
    week = eastern_week_bounds_utc(moment)
    keys = _cards_for_club(db)
    settings = _settings_by_key(db)
    payments = _load_payments(db)
    sends = _load_sends(db)
    grouped: dict[str, list[AccountBalanceCard]] = {"venmo": [], "cashapp": []}
    for method, tag in keys:
        grouped[method].append(
            _card(
                method,
                tag,
                settings=settings.get((method, tag)),
                payments=payments.get((method, tag), []),
                sends=sends.get((method, tag), []),
                week_start=week.start_utc,
                week_end=week.end_utc,
                now=moment,
            )
        )
    for cards in grouped.values():
        cards.sort(
            key=lambda card: (
                -max(card.balance_cents, card.sending_remaining_cents),
                card.tag,
            )
        )
    return grouped


def list_blocking_sends(
    db: Session, method: str, tag: str, *, now: datetime | None = None
) -> list[BlockingSend]:
    method_slug = normalize_method(method)
    stored = normalize_tag(method_slug, tag)
    if (
        not stored
        or _excluded(stored)
        or (method_slug, stored) not in _cards_for_club(db)
    ):
        raise LookupError("account not found")
    moment = _aware(now)
    sends = _load_sends(db).get((method_slug, stored), [])
    blocking = [row for row in sends if _blocking(row, moment) and row.occurred_at]
    blocking.sort(key=lambda row: (row.occurred_at, row.id), reverse=True)
    return [
        BlockingSend(
            id=row.id,
            amount_cents=row.amount_cents,
            recipient=row.recipient,
            occurred_at=row.occurred_at,
            expires_at=row.occurred_at + WINDOW,
        )
        for row in blocking
        if row.occurred_at is not None
    ]


def update_account_balance_settings(
    db: Session,
    method: str,
    tag: str,
    *,
    offset_cents: int,
    sending_ceiling_cents: int,
    inflow_max_cents: int,
    now: datetime | None = None,
) -> AccountBalanceCard:
    method_slug = normalize_method(method)
    stored = normalize_tag(method_slug, tag)
    if not stored or stored in {"@", "$"}:
        raise ValueError("tag is required")
    if _excluded(stored) or (method_slug, stored) not in _cards_for_club(db):
        raise LookupError("account not found")
    if sending_ceiling_cents <= 0:
        raise ValueError("sending ceiling must be greater than zero")
    if inflow_max_cents <= 0:
        raise ValueError("inflow max must be greater than zero")
    row = (
        db.query(AccountBalanceSettings)
        .filter_by(method=method_slug, tag=stored)
        .one_or_none()
    )
    if row is None:
        row = AccountBalanceSettings(
            method=method_slug,
            tag=stored,
            offset_cents=int(offset_cents),
            sending_ceiling_cents=int(sending_ceiling_cents),
            inflow_max_cents=int(inflow_max_cents),
        )
        db.add(row)
    else:
        row.offset_cents = int(offset_cents)
        row.sending_ceiling_cents = int(sending_ceiling_cents)
        row.inflow_max_cents = int(inflow_max_cents)
    db.flush()
    cards = list_account_balances(db, now=now)[method_slug]
    for card in cards:
        if card.tag == stored:
            return card
    raise LookupError("account not found")
