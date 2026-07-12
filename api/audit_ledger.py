"""Shared ledger event fetchers for audit reconcile and export.

Reconcile ledger signs match ClubGG trade record (club chip-ledger perspective):
negative = chips/money sent to the player; positive = chips claimed back.
Postgres payment rows store positive USD magnitudes; aggregate_ledger_by_player
negates club-to-player outflows (deposits, RB, bonuses) and keeps cashouts positive
(player returning chips to the club).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Callable, Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from api.club_audit_timezone import audit_day_window_utc, occurred_at_in_audit_day
from api.club_slug import resolve_club_id, slug_for_club_id
from api.payments_helpers import (
    apply_analytics_payment_exclusion,
    build_cashapp_payment_read,
    build_crypto_payment_read,
    build_paypal_payment_read,
    build_venmo_payment_read,
    build_zelle_payment_read,
    resolve_group_title,
)
from bot.services.player_details import parse_group_title_parts
from bot.services.staff_cashout_records import _gg_player_id_from_title
from db.models import (
    BonusRecord,
    CashAppPayment,
    CryptoPayment,
    EarlyRakebackLine,
    EarlyRakebackSnapshot,
    PayPalPayment,
    PlayerDetails,
    StaffCashoutRecord,
    StripeCheckoutSession,
    StripeCustomer,
    VenmoPayment,
    ZellePayment,
)

LedgerSource = Literal[
    "deposit_stripe",
    "deposit_zelle",
    "deposit_venmo",
    "deposit_cashapp",
    "deposit_paypal",
    "deposit_crypto",
    "early_rakeback",
    "bonus",
    "cashout",
    "monday_settlement",
]

LEDGER_SOURCE_LABELS: dict[str, str] = {
    "deposit_stripe": "Stripe",
    "deposit_zelle": "Zelle",
    "deposit_venmo": "Venmo",
    "deposit_cashapp": "Cash App",
    "deposit_paypal": "PayPal",
    "deposit_crypto": "Crypto",
    "early_rakeback": "Early RB",
    "bonus": "Bonus",
    "monday_settlement": "RB settlement (Monday)",
    "cashout": "Cashout",
}

DEPOSIT_METHOD_ORDER: tuple[str, ...] = (
    "deposit_stripe",
    "deposit_zelle",
    "deposit_venmo",
    "deposit_cashapp",
    "deposit_paypal",
    "deposit_crypto",
)

# Audit-export Tag column fields for Matching Variant.
_MANUAL_DEPOSIT_TAG_FIELDS: dict[str, str] = {
    "deposit_zelle": "zelle_recipient",
    "deposit_venmo": "venmo_handle",
    "deposit_cashapp": "cashapp_handle",
    "deposit_paypal": "paypal_email",
    "deposit_crypto": "token_symbol",
}


@dataclass(frozen=True)
class LedgerEvent:
    source: LedgerSource
    gg_player_id: str | None
    amount_usd: Decimal
    occurred_at_utc: datetime | None
    external_id: str
    detail: str | None = None
    display_name: str | None = None
    variant: str | None = None


@dataclass(frozen=True)
class LedgerBreakdown:
    deposits: Decimal = Decimal(0)
    early_rb: Decimal = Decimal(0)
    bonuses: Decimal = Decimal(0)
    monday: Decimal = Decimal(0)
    cashouts: Decimal = Decimal(0)

    @property
    def net(self) -> Decimal:
        return (
            self.deposits
            + self.early_rb
            + self.bonuses
            + self.monday
            + self.cashouts
        )


@dataclass(frozen=True)
class LedgerLine:
    gg_player_id: str | None
    member_nickname: str | None
    source: str
    source_label: str
    amount_signed: Decimal
    occurred_at_utc: datetime | None
    external_id: str
    detail: str | None = None
    display_name: str | None = None
    variant: str | None = None


def _club_outflow_usd(amount: Decimal) -> Decimal:
    """Club-to-player outflow as a negative reconcile amount."""
    return -abs(amount)


def _club_inflow_usd(amount: Decimal) -> Decimal:
    """Player-to-club inflow (cashout) as a positive reconcile amount."""
    return abs(amount)


def ledger_source_label(source: str) -> str:
    return LEDGER_SOURCE_LABELS.get(source, source.replace("_", " ").title())


def signed_reconcile_amount(source: str, amount_usd: Decimal) -> Decimal:
    if source == "cashout":
        return _club_inflow_usd(amount_usd)
    if source.startswith("deposit_") or source in (
        "early_rakeback",
        "bonus",
        "monday_settlement",
    ):
        return _club_outflow_usd(amount_usd)
    return amount_usd


def build_ledger_lines(
    events: list[LedgerEvent],
    nicknames: dict[str, str | None] | None = None,
) -> list[LedgerLine]:
    nickname_map = nicknames or {}
    lines: list[LedgerLine] = []
    for event in events:
        gid = (event.gg_player_id or "").strip() or None
        lines.append(
            LedgerLine(
                gg_player_id=gid,
                member_nickname=nickname_map.get(gid) if gid else None,
                source=event.source,
                source_label=ledger_source_label(event.source),
                amount_signed=signed_reconcile_amount(event.source, event.amount_usd),
                occurred_at_utc=event.occurred_at_utc,
                external_id=event.external_id,
                detail=event.detail,
                display_name=event.display_name,
                variant=event.variant,
            )
        )
    return lines


def slug_for_payment_club(
    session: Session,
    club_id: int | None,
    data: dict | None = None,
) -> str:
    if club_id is not None:
        slug = slug_for_club_id(session, int(club_id))
        if slug:
            return slug
    if data:
        title = str(data.get("group_title") or "").strip()
        if title:
            parsed = parse_group_title_parts(title)
            if parsed:
                for token in sorted(parsed.shorthands):
                    if token == "AT":
                        return "aces-table"
                    if token == "RT":
                        return "round-table"
                    if token == "GTO":
                        return "clubgto"
                    if token == "CC":
                        return "creator-club"
    return "round-table"


def payment_in_audit_day_for_club(
    session: Session,
    *,
    club_slug: str,
    audit_date: date | str,
    club_id: int | None,
    occurred_at: datetime | None,
    data: dict | None = None,
) -> bool:
    if occurred_at is None:
        return False
    slug = slug_for_payment_club(session, club_id, data)
    if slug != club_slug.strip().lower():
        return False
    return occurred_at_in_audit_day(occurred_at, slug, audit_date)


def _apply_audit_manual_filters(
    session: Session,
    query,
    payment_cls,
    *,
    from_dt: datetime,
    to_dt: datetime,
):
    query = query.filter(
        payment_cls.is_test.is_(False),
        payment_cls.created_at >= from_dt,
        payment_cls.created_at <= to_dt,
    )
    return apply_analytics_payment_exclusion(
        session, query, payment_cls.telegram_chat_id
    )


def _apply_audit_stripe_filters(query, *, from_dt: datetime, to_dt: datetime):
    effective_dt = func.coalesce(
        StripeCheckoutSession.completed_at,
        StripeCheckoutSession.created_at,
    )
    return query.filter(
        StripeCheckoutSession.status == "complete",
        effective_dt >= from_dt,
        effective_dt <= to_dt,
    )


def _resolve_bonus_gg_player_id(
    session: Session,
    club_id: int | None,
    player_username: str,
) -> str | None:
    username = (player_username or "").strip().lstrip("@")
    if not username:
        return None
    if club_id is None:
        return None
    row = (
        session.query(PlayerDetails)
        .filter(
            PlayerDetails.club_id == int(club_id),
            func.lower(PlayerDetails.gg_nickname) == username.lower(),
        )
        .first()
    )
    if row:
        return (row.gg_player_id or "").strip() or None
    return None


def _fetch_manual_deposit_events(
    session: Session,
    payment_cls,
    build_read: Callable,
    *,
    club_slug: str,
    audit_date: date,
    from_dt: datetime,
    to_dt: datetime,
    source: LedgerSource,
) -> list[LedgerEvent]:
    query = _apply_audit_manual_filters(
        session,
        session.query(payment_cls),
        payment_cls,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    rows = query.order_by(payment_cls.created_at.desc(), payment_cls.id.desc()).all()
    out: list[LedgerEvent] = []
    for row in rows:
        data = build_read(session, row)
        occurred_at = data.get("created_at")
        if not payment_in_audit_day_for_club(
            session,
            club_slug=club_slug,
            audit_date=audit_date,
            club_id=data.get("club_id"),
            occurred_at=occurred_at,
            data=data,
        ):
            continue
        gg_id = (data.get("gg_player_id") or "").strip() or None
        if not gg_id:
            chat_id = data.get("telegram_chat_id")
            if chat_id is not None:
                _, resolved = resolve_group_title(
                    session,
                    chat_id,
                    fallback_gg_player_id=None,
                )
                gg_id = (resolved or "").strip() or None
        amount = data.get("amount_usd")
        amount_usd = Decimal(str(amount)) if amount is not None else Decimal(0)
        payer = (
            str(data.get("payer_name") or data.get("from_label") or "").strip() or None
        )
        tag_field = _MANUAL_DEPOSIT_TAG_FIELDS.get(source)
        tag = (
            str(data.get(tag_field) or "").strip() or None if tag_field else None
        )
        out.append(
            LedgerEvent(
                source=source,
                gg_player_id=gg_id,
                amount_usd=amount_usd,
                occurred_at_utc=occurred_at,
                external_id=f"{source}:{row.id}",
                display_name=payer,
                variant=tag,
            )
        )
    return out


def fetch_deposit_events(
    session: Session,
    *,
    club_slug: str,
    audit_date: date,
) -> list[LedgerEvent]:
    slug = club_slug.strip().lower()
    from_dt, to_dt = audit_day_window_utc(slug, audit_date)
    events: list[LedgerEvent] = []

    stripe_query = _apply_audit_stripe_filters(
        session.query(StripeCheckoutSession),
        from_dt=from_dt,
        to_dt=to_dt,
    )
    stripe_rows = stripe_query.order_by(
        StripeCheckoutSession.completed_at.desc().nullslast(),
        StripeCheckoutSession.id.desc(),
    ).all()
    customer_by_stripe_id: dict[str, StripeCustomer] = {}
    if stripe_rows:
        customer_ids = {row.stripe_customer_id for row in stripe_rows}
        for cust in (
            session.query(StripeCustomer)
            .filter(StripeCustomer.stripe_customer_id.in_(customer_ids))
            .all()
        ):
            customer_by_stripe_id[cust.stripe_customer_id] = cust

    for row in stripe_rows:
        completed = row.completed_at or row.created_at
        if not payment_in_audit_day_for_club(
            session,
            club_slug=slug,
            audit_date=audit_date,
            club_id=row.club_id,
            occurred_at=completed,
        ):
            continue
        cust = customer_by_stripe_id.get(row.stripe_customer_id)
        fallback_gg = cust.gg_player_id if cust else None
        if row.telegram_chat_id is not None:
            _, gg_id = resolve_group_title(
                session,
                row.telegram_chat_id,
                fallback_gg_player_id=fallback_gg,
            )
        else:
            gg_id = fallback_gg
        events.append(
            LedgerEvent(
                source="deposit_stripe",
                gg_player_id=(gg_id or "").strip() or None,
                amount_usd=Decimal(row.amount_cents) / Decimal(100),
                occurred_at_utc=completed,
                external_id=f"deposit_stripe:{row.id}",
            )
        )

    tagged_sources = [
        (ZellePayment, build_zelle_payment_read, "deposit_zelle"),
        (VenmoPayment, build_venmo_payment_read, "deposit_venmo"),
        (CashAppPayment, build_cashapp_payment_read, "deposit_cashapp"),
        (PayPalPayment, build_paypal_payment_read, "deposit_paypal"),
    ]
    for payment_cls, build_read, source in tagged_sources:
        events.extend(
            _fetch_manual_deposit_events(
                session,
                payment_cls,
                build_read,
                club_slug=slug,
                audit_date=audit_date,
                from_dt=from_dt,
                to_dt=to_dt,
                source=source,  # type: ignore[arg-type]
            )
        )

    events.extend(
        _fetch_manual_deposit_events(
            session,
            CryptoPayment,
            build_crypto_payment_read,
            club_slug=slug,
            audit_date=audit_date,
            from_dt=from_dt,
            to_dt=to_dt,
            source="deposit_crypto",
        )
    )
    return events


def fetch_early_rakeback_events(
    session: Session,
    *,
    club_slug: str,
    audit_date: date,
) -> list[LedgerEvent]:
    slug = club_slug.strip().lower()
    snapshot = (
        session.query(EarlyRakebackSnapshot)
        .filter_by(club_slug=slug, audit_date=audit_date)
        .first()
    )
    if not snapshot:
        return []
    lines = (
        session.query(EarlyRakebackLine)
        .filter_by(snapshot_id=snapshot.id)
        .order_by(EarlyRakebackLine.id.asc())
        .all()
    )
    return [
        LedgerEvent(
            source="early_rakeback",
            gg_player_id=(line.gg_player_id or "").strip() or None,
            amount_usd=Decimal(str(line.amount_usd)),
            occurred_at_utc=line.occurred_at,
            external_id=f"early_rakeback:{line.id}",
        )
        for line in lines
    ]


def _bonus_display_name(record: BonusRecord) -> str:
    title = (record.group_title or "").strip()
    if title:
        from cashier.services.zapier import build_zapier_name

        formatted = build_zapier_name(title)
        return formatted or title
    return str(record.player_username).strip()


def _bonus_variant_cell(record: BonusRecord) -> str:
    type_name = (record.bonus_type.name if record.bonus_type else "").strip()
    desc = (record.custom_description or "").strip()
    if type_name and desc:
        return f"{type_name} — {desc}"
    return type_name or desc


def fetch_bonus_events(
    session: Session,
    *,
    club_slug: str,
    audit_date: date,
) -> list[LedgerEvent]:
    slug = club_slug.strip().lower()
    club_id = resolve_club_id(session, slug)
    from_dt, to_dt = audit_day_window_utc(slug, audit_date)
    rows = (
        session.query(BonusRecord)
        .filter(
            BonusRecord.club_id == club_id,
            BonusRecord.created_at >= from_dt,
            BonusRecord.created_at <= to_dt,
        )
        .order_by(BonusRecord.created_at.desc(), BonusRecord.id.desc())
        .all()
    )
    out: list[LedgerEvent] = []
    for row in rows:
        if not payment_in_audit_day_for_club(
            session,
            club_slug=slug,
            audit_date=audit_date,
            club_id=row.club_id,
            occurred_at=row.created_at,
        ):
            continue
        gg_id = (row.gg_player_id or "").strip() or _resolve_bonus_gg_player_id(
            session, row.club_id, str(row.player_username)
        )
        detail = (row.group_title or str(row.player_username)).strip()
        display = _bonus_display_name(row)
        variant = _bonus_variant_cell(row) or None
        out.append(
            LedgerEvent(
                source="bonus",
                gg_player_id=gg_id,
                amount_usd=Decimal(str(row.amount)),
                occurred_at_utc=row.created_at,
                external_id=f"bonus:{row.id}",
                detail=detail,
                display_name=display or None,
                variant=variant,
            )
        )
    return out


def fetch_cashout_events(
    session: Session,
    *,
    club_slug: str,
    audit_date: date,
) -> list[LedgerEvent]:
    slug = club_slug.strip().lower()
    club_id = resolve_club_id(session, slug)
    from_dt, to_dt = audit_day_window_utc(slug, audit_date)
    rows = (
        session.query(StaffCashoutRecord)
        .filter(
            StaffCashoutRecord.club_id == club_id,
            StaffCashoutRecord.created_at >= from_dt,
            StaffCashoutRecord.created_at <= to_dt,
        )
        .order_by(StaffCashoutRecord.created_at.desc(), StaffCashoutRecord.id.desc())
        .all()
    )
    out: list[LedgerEvent] = []
    for row in rows:
        if not payment_in_audit_day_for_club(
            session,
            club_slug=slug,
            audit_date=audit_date,
            club_id=row.club_id,
            occurred_at=row.created_at,
        ):
            continue
        gg_id = (row.gg_player_id or "").strip() or _gg_player_id_from_title(
            row.group_title
        )
        out.append(
            LedgerEvent(
                source="cashout",
                gg_player_id=gg_id,
                amount_usd=Decimal(str(row.amount)),
                occurred_at_utc=row.created_at,
                external_id=f"cashout:{row.id}",
            )
        )
    return out


def aggregate_ledger_by_player(
    events: list[LedgerEvent],
) -> tuple[dict[str, LedgerBreakdown], list[LedgerEvent]]:
    """Return per-player breakdown and unmatched events (no gg_player_id)."""
    by_player: dict[str, LedgerBreakdown] = {}
    unmatched: list[LedgerEvent] = []

    for event in events:
        if not event.gg_player_id:
            unmatched.append(event)
            continue
        pid = event.gg_player_id
        current = by_player.get(pid, LedgerBreakdown())
        signed = signed_reconcile_amount(event.source, event.amount_usd)
        if event.source.startswith("deposit_"):
            current = LedgerBreakdown(
                deposits=current.deposits + signed,
                early_rb=current.early_rb,
                bonuses=current.bonuses,
                monday=current.monday,
                cashouts=current.cashouts,
            )
        elif event.source == "early_rakeback":
            current = LedgerBreakdown(
                deposits=current.deposits,
                early_rb=current.early_rb + signed,
                bonuses=current.bonuses,
                monday=current.monday,
                cashouts=current.cashouts,
            )
        elif event.source == "bonus":
            current = LedgerBreakdown(
                deposits=current.deposits,
                early_rb=current.early_rb,
                bonuses=current.bonuses + signed,
                monday=current.monday,
                cashouts=current.cashouts,
            )
        elif event.source == "monday_settlement":
            current = LedgerBreakdown(
                deposits=current.deposits,
                early_rb=current.early_rb,
                bonuses=current.bonuses,
                monday=current.monday + signed,
                cashouts=current.cashouts,
            )
        elif event.source == "cashout":
            current = LedgerBreakdown(
                deposits=current.deposits,
                early_rb=current.early_rb,
                bonuses=current.bonuses,
                monday=current.monday,
                cashouts=current.cashouts + signed,
            )
        else:
            continue
        by_player[pid] = current

    return by_player, unmatched
