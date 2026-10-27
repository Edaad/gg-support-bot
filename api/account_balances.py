"""Round Table Venmo, Cash App, PayPal, and crypto balance cards."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
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
    CryptoPayment,
    OutboundSend,
    PayPalPayment,
    VenmoPayment,
)

METHODS = ("venmo", "cashapp", "paypal", "crypto")
VARIANT_METHODS = ("venmo", "cashapp")
NAMED_METHODS = ("paypal", "crypto")
METHOD_OWNER = "round-table"
PAYPAL_BALANCE_EMAIL = "aiden@concordconsultinginc.com"
# Ingests before the address was corrected omitted "inc".
_PAYPAL_LEGACY_EMAIL = "aiden@concordconsulting.com"
_PAYPAL_BALANCE_EMAILS = frozenset({PAYPAL_BALANCE_EMAIL, _PAYPAL_LEGACY_EMAIL})
EXCLUDED_HANDLES = frozenset({"janseashells", "creatorclub"})
WINDOW = timedelta(hours=168)
DEFAULT_OFFSET_CENTS = 0
DEFAULT_INFLOW_MAX_CENTS = 500_000
DEFAULT_SENDING_CEILING_CENTS = {
    "venmo": 1_000_000,
    "cashapp": 500_000,
    "paypal": 500_000,
    "crypto": 500_000,
}
LABEL_MAX_LENGTH = 100
CHAIN_ORDER = ("ethereum", "bsc", "base", "bitcoin", "solana", "tron", "ripple")
CHAIN_LABELS = {
    "ethereum": "Ethereum",
    "bsc": "BSC",
    "base": "Base",
    "bitcoin": "Bitcoin",
    "solana": "Solana",
    "tron": "Tron",
    "ripple": "Ripple",
}


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
    label: str
    detail: str | None
    custom_label: str | None
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


@dataclass
class _Wallet:
    tag: str
    detail: str
    chains: set[str] = field(default_factory=set)
    first_at: datetime | None = None
    payments: list[_Money] = field(default_factory=list)


def normalize_method(value: str) -> str:
    method = (value or "").strip().lower()
    if method not in METHODS:
        allowed = ", ".join(METHODS)
        raise ValueError(f"method must be one of: {allowed}")
    return method


def normalize_tag(method: str, raw: str | None) -> str:
    if method == "venmo":
        return normalize_venmo_tag(raw)
    if method == "cashapp":
        return normalize_cashapp_tag(raw)
    if method == "paypal":
        return (raw or "").strip().lower()
    if method == "crypto":
        return _crypto_key(raw)
    return ""


def _crypto_key(raw: str | None) -> str:
    text = (raw or "").strip()
    if text.lower().startswith("0x"):
        return text.lower()
    return text


def _chain_label(chain: str) -> str:
    return CHAIN_LABELS.get(chain, chain.title() if chain else "Crypto")


def _chain_names(chains: set[str]) -> list[str]:
    known = [chain for chain in CHAIN_ORDER if chain in chains]
    extra = sorted(chain for chain in chains if chain not in CHAIN_ORDER)
    return [_chain_label(chain) for chain in known + extra]


def _crypto_headers(wallets: dict[str, _Wallet]) -> dict[str, str]:
    singles: dict[str, list[_Wallet]] = {}
    for wallet in wallets.values():
        if len(wallet.chains) != 1:
            continue
        chain = next(iter(wallet.chains))
        singles.setdefault(chain, []).append(wallet)
    numbered: dict[str, str] = {}
    for chain, group in singles.items():
        if len(group) < 2:
            continue
        group.sort(
            key=lambda wallet: (
                wallet.first_at or datetime.max.replace(tzinfo=timezone.utc),
                wallet.tag,
            )
        )
        name = _chain_label(chain)
        for index, wallet in enumerate(group, start=1):
            numbered[wallet.tag] = f"{name} #{index}"
    headers: dict[str, str] = {}
    for tag, wallet in wallets.items():
        if tag in numbered:
            headers[tag] = numbered[tag]
            continue
        names = _chain_names(wallet.chains)
        headers[tag] = ", ".join(names) if names else "Crypto"
    return headers


def _clean_label(label: str | None) -> str | None:
    text = (label or "").strip()
    if not text:
        return None
    if len(text) > LABEL_MAX_LENGTH:
        raise ValueError("label is too long")
    return text


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
        .filter(ClubPaymentMethod.slug.in_(VARIANT_METHODS))
        .all()
    )
    found: dict[tuple[str, str], None] = {}
    for slug, variant in rows:
        method = (slug or "").strip().lower()
        if method not in VARIANT_METHODS:
            continue
        tag = _variant_tag(method, variant)
        if not tag or tag in {"@", "$"} or _excluded(tag):
            continue
        found[(method, tag)] = None
    return found


def _variant_names(db: Session) -> dict[tuple[str, str], str]:
    """Round Table PayPal or crypto variant label, keyed by normalized tag."""
    rows = (
        db.query(ClubPaymentMethod.slug, ClubPaymentTierVariant)
        .join(
            ClubPaymentTierVariant,
            ClubPaymentTierVariant.method_id == ClubPaymentMethod.id,
        )
        .join(Club, Club.id == ClubPaymentMethod.club_id)
        .filter(Club.name.ilike(ROUND_TABLE_CLUB_NAME))
        .filter(ClubPaymentMethod.slug.in_(NAMED_METHODS))
        .order_by(ClubPaymentTierVariant.id)
        .all()
    )
    found: dict[tuple[str, str], str] = {}
    for slug, variant in rows:
        method = (slug or "").strip().lower()
        tag = normalize_tag(method, variant.tag)
        name = (variant.label or "").strip()
        if not tag or not name or (method, tag) in found:
            continue
        found[(method, tag)] = name
    return found


def _settings_by_key(db: Session) -> dict[tuple[str, str], AccountBalanceSettings]:
    rows = db.query(AccountBalanceSettings).all()
    return {(row.method, row.tag): row for row in rows}


def _paypal_balance_settings(
    settings: dict[tuple[str, str], AccountBalanceSettings],
) -> AccountBalanceSettings | None:
    return settings.get(("paypal", PAYPAL_BALANCE_EMAIL)) or settings.get(
        ("paypal", _PAYPAL_LEGACY_EMAIL)
    )


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


def _paypal_payments(db: Session) -> list[_Money]:
    rows = (
        db.query(
            PayPalPayment.amount_cents,
            PayPalPayment.paid_at,
            PayPalPayment.created_at,
        )
        .filter(
            PayPalPayment.method_owner == METHOD_OWNER,
            PayPalPayment.is_test.is_(False),
            func.lower(PayPalPayment.paypal_email).in_(_PAYPAL_BALANCE_EMAILS),
        )
        .all()
    )
    return [
        _Money(int(amount_cents), _event_at(paid_at, created_at))
        for amount_cents, paid_at, created_at in rows
    ]


def _crypto_wallets(db: Session) -> dict[str, _Wallet]:
    rows = (
        db.query(
            CryptoPayment.to_address,
            CryptoPayment.chain,
            CryptoPayment.amount_cents,
            CryptoPayment.paid_at,
            CryptoPayment.created_at,
        )
        .filter(
            CryptoPayment.method_owner == METHOD_OWNER,
            CryptoPayment.is_test.is_(False),
        )
        .all()
    )
    wallets: dict[str, _Wallet] = {}
    for to_address, chain, amount_cents, paid_at, created_at in rows:
        detail = (to_address or "").strip()
        tag = _crypto_key(detail)
        if not tag:
            continue
        wallet = wallets.get(tag)
        if wallet is None:
            wallet = _Wallet(tag=tag, detail=detail)
            wallets[tag] = wallet
        created = _aware(created_at)
        if wallet.first_at is None or created < wallet.first_at:
            wallet.first_at = created
            wallet.detail = detail
        chain_key = (chain or "").strip().lower()
        if chain_key:
            wallet.chains.add(chain_key)
        wallet.payments.append(
            _Money(int(amount_cents), _event_at(paid_at, created_at))
        )
    return wallets


def _payment_ledger(
    db: Session,
) -> tuple[dict[tuple[str, str], list[_Money]], dict[str, _Wallet]]:
    grouped = _load_payments(db)
    paypal = _paypal_payments(db)
    if paypal:
        grouped[("paypal", PAYPAL_BALANCE_EMAIL)] = paypal
    wallets = _crypto_wallets(db)
    for tag, wallet in wallets.items():
        grouped[("crypto", tag)] = wallet.payments
    return grouped, wallets


def _account_keys(db: Session) -> set[tuple[str, str]]:
    payments, _wallets = _payment_ledger(db)
    keys = set(_cards_for_club(db))
    if ("paypal", PAYPAL_BALANCE_EMAIL) in payments:
        keys.add(("paypal", PAYPAL_BALANCE_EMAIL))
    keys.update(("crypto", tag) for tag in _wallets)
    return keys


def _load_sends(db: Session) -> dict[tuple[str, str], list[_Send]]:
    rows = (
        db.query(OutboundSend)
        .filter(
            OutboundSend.method.in_(("venmo", "cashapp", "crypto")),
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


def _shown_label(
    method: str,
    tag: str,
    default: str,
    settings: AccountBalanceSettings | None,
    variant_names: dict[tuple[str, str], str],
) -> tuple[str, str | None]:
    custom = _clean_label(settings.label) if settings is not None else None
    if custom:
        return custom, custom
    if method in NAMED_METHODS and (method, tag) in variant_names:
        return variant_names[(method, tag)], None
    return default, None


def _card(
    method: str,
    tag: str,
    *,
    default_label: str,
    detail: str | None,
    settings: AccountBalanceSettings | None,
    variant_names: dict[tuple[str, str], str],
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
    label, custom_label = _shown_label(
        method, tag, default_label, settings, variant_names
    )
    return AccountBalanceCard(
        method=method,
        tag=tag,
        label=label,
        detail=detail,
        custom_label=custom_label,
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
    settings = _settings_by_key(db)
    names = _variant_names(db)
    payments, wallets = _payment_ledger(db)
    sends = _load_sends(db)
    headers = _crypto_headers(wallets)
    grouped: dict[str, list[AccountBalanceCard]] = {method: [] for method in METHODS}
    for method, tag in _cards_for_club(db):
        grouped[method].append(
            _card(
                method,
                tag,
                default_label=tag,
                detail=None,
                settings=settings.get((method, tag)),
                variant_names=names,
                payments=payments.get((method, tag), []),
                sends=sends.get((method, tag), []),
                week_start=week.start_utc,
                week_end=week.end_utc,
                now=moment,
            )
        )
    if ("paypal", PAYPAL_BALANCE_EMAIL) in payments:
        grouped["paypal"].append(
            _card(
                "paypal",
                PAYPAL_BALANCE_EMAIL,
                default_label=PAYPAL_BALANCE_EMAIL,
                detail=None,
                settings=_paypal_balance_settings(settings),
                variant_names=names,
                payments=payments[("paypal", PAYPAL_BALANCE_EMAIL)],
                sends=sends.get(("paypal", PAYPAL_BALANCE_EMAIL), []),
                week_start=week.start_utc,
                week_end=week.end_utc,
                now=moment,
            )
        )
    for tag, wallet in wallets.items():
        grouped["crypto"].append(
            _card(
                "crypto",
                tag,
                default_label=headers[tag],
                detail=wallet.detail,
                settings=settings.get(("crypto", tag)),
                variant_names=names,
                payments=wallet.payments,
                sends=sends.get(("crypto", tag), []),
                week_start=week.start_utc,
                week_end=week.end_utc,
                now=moment,
            )
        )
    for cards in grouped.values():
        cards.sort(
            key=lambda card: (
                -min(card.balance_cents, card.sending_remaining_cents),
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
        or (method_slug in VARIANT_METHODS and _excluded(stored))
        or (method_slug, stored) not in _account_keys(db)
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


def offset_cents_for_balance(
    db: Session, method: str, tag: str, balance_cents: int
) -> int:
    """Offset that makes the live balance equal the reported account balance."""
    method_slug = normalize_method(method)
    stored = normalize_tag(method_slug, tag)
    payments, _wallets = _payment_ledger(db)
    received = sum(row.amount_cents for row in payments.get((method_slug, stored), []))
    sent = sum(
        row.amount_cents for row in _load_sends(db).get((method_slug, stored), [])
    )
    return int(balance_cents) - (received - sent)


def update_account_balance_settings(
    db: Session,
    method: str,
    tag: str,
    *,
    offset_cents: int,
    sending_ceiling_cents: int,
    inflow_max_cents: int,
    label: str | None = None,
    set_label: bool = False,
    now: datetime | None = None,
) -> AccountBalanceCard:
    method_slug = normalize_method(method)
    stored = normalize_tag(method_slug, tag)
    if not stored or stored in {"@", "$"}:
        raise ValueError("tag is required")
    if (method_slug in VARIANT_METHODS and _excluded(stored)) or (
        method_slug,
        stored,
    ) not in _account_keys(db):
        raise LookupError("account not found")
    if sending_ceiling_cents <= 0:
        raise ValueError("sending ceiling must be greater than zero")
    if inflow_max_cents <= 0:
        raise ValueError("inflow max must be greater than zero")
    stored_label = _clean_label(label) if set_label else None
    row = (
        db.query(AccountBalanceSettings)
        .filter_by(method=method_slug, tag=stored)
        .one_or_none()
    )
    if row is None:
        row = AccountBalanceSettings(
            method=method_slug,
            tag=stored,
            label=stored_label,
            offset_cents=int(offset_cents),
            sending_ceiling_cents=int(sending_ceiling_cents),
            inflow_max_cents=int(inflow_max_cents),
        )
        db.add(row)
    else:
        row.offset_cents = int(offset_cents)
        row.sending_ceiling_cents = int(sending_ceiling_cents)
        row.inflow_max_cents = int(inflow_max_cents)
        if set_label:
            row.label = stored_label
    db.flush()
    cards = list_account_balances(db, now=now)[method_slug]
    for card in cards:
        if card.tag == stored:
            return card
    raise LookupError("account not found")
