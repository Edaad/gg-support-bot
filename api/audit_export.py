"""Cross-club audit export: one XLSX workbook with a sheet per payment provider."""

from __future__ import annotations

import io
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.payments_helpers import (
    apply_analytics_payment_exclusion,
    build_cashapp_payment_read,
    build_crypto_payment_read,
    build_paypal_payment_read,
    build_venmo_payment_read,
    build_zelle_payment_read,
    lookup_gg_nickname,
    resolve_group_title,
    resolve_method_display,
)
from db.models import (
    CashAppPayment,
    Club,
    CryptoPayment,
    PayPalPayment,
    StripeCheckoutSession,
    StripeCustomer,
    VenmoPayment,
    ZellePayment,
)

STRIPE_HEADERS = [
    "completed_at",
    "club_name",
    "group_title",
    "gg_nickname",
    "gg_player_id",
    "method_name",
    "amount_usd",
    "stripe_fee",
    "currency",
    "stripe_payment_intent_id",
    "stripe_checkout_session_id",
]

VENMO_HEADERS = [
    "created_at",
    "club_name",
    "payer_name",
    "venmo_handle",
    "group_title",
    "gg_nickname",
    "gg_player_id",
    "amount_usd",
    "status",
    "auto_bound",
    "goods_or_services",
]

ZELLE_HEADERS = [
    "created_at",
    "club_name",
    "payer_name",
    "zelle_recipient",
    "group_title",
    "gg_nickname",
    "gg_player_id",
    "amount_usd",
    "status",
    "auto_bound",
]

CASHAPP_HEADERS = [
    "created_at",
    "club_name",
    "payer_name",
    "cashapp_handle",
    "group_title",
    "gg_nickname",
    "gg_player_id",
    "amount_usd",
    "status",
    "auto_bound",
]

PAYPAL_HEADERS = [
    "created_at",
    "club_name",
    "payer_name",
    "paypal_email",
    "group_title",
    "gg_nickname",
    "gg_player_id",
    "amount_usd",
    "status",
    "auto_bound",
]

CRYPTO_HEADERS = [
    "created_at",
    "club_name",
    "from_label",
    "chain",
    "token_symbol",
    "to_address",
    "transaction_hash",
    "group_title",
    "gg_nickname",
    "gg_player_id",
    "amount_usd",
    "status",
]

SHEET_SPECS: list[tuple[str, list[str]]] = [
    ("Stripe", STRIPE_HEADERS),
    ("Venmo", VENMO_HEADERS),
    ("Zelle", ZELLE_HEADERS),
    ("Cash App", CASHAPP_HEADERS),
    ("PayPal", PAYPAL_HEADERS),
    ("Crypto", CRYPTO_HEADERS),
]


def _stripe_fee_usd(amount_cents: int) -> Decimal:
    return Decimal(round(amount_cents * 0.029 + 30)) / Decimal(100)


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat()


def _fmt_decimal(value: Decimal | int | float | None) -> str | float:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return float(value)
    return value


def _club_name_map(session: Session) -> dict[int, str]:
    return {int(row.id): str(row.name) for row in session.query(Club.id, Club.name).all()}


def _club_name(club_names: dict[int, str], club_id: int | None) -> str:
    if club_id is None:
        return ""
    return club_names.get(int(club_id), "")


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


def _write_sheet(
    ws: Worksheet,
    headers: list[str],
    rows: list[list[Any]],
) -> None:
    ws.append(headers)
    for row in rows:
        ws.append(row)
    if headers:
        ws.auto_filter.ref = ws.dimensions


def build_audit_workbook(session: Session, from_dt: datetime, to_dt: datetime) -> bytes:
    club_names = _club_name_map(session)
    wb = Workbook()
    wb.remove(wb.active)

    stripe_rows = _fetch_stripe_rows(session, club_names, from_dt, to_dt)
    venmo_rows = _fetch_manual_rows(
        session, club_names, VenmoPayment, build_venmo_payment_read, from_dt, to_dt, _venmo_row
    )
    zelle_rows = _fetch_manual_rows(
        session, club_names, ZellePayment, build_zelle_payment_read, from_dt, to_dt, _zelle_row
    )
    cashapp_rows = _fetch_manual_rows(
        session,
        club_names,
        CashAppPayment,
        build_cashapp_payment_read,
        from_dt,
        to_dt,
        _cashapp_row,
    )
    paypal_rows = _fetch_manual_rows(
        session,
        club_names,
        PayPalPayment,
        build_paypal_payment_read,
        from_dt,
        to_dt,
        _paypal_row,
    )
    crypto_rows = _fetch_manual_rows(
        session,
        club_names,
        CryptoPayment,
        build_crypto_payment_read,
        from_dt,
        to_dt,
        _crypto_row,
    )

    all_rows = [
        stripe_rows,
        venmo_rows,
        zelle_rows,
        cashapp_rows,
        paypal_rows,
        crypto_rows,
    ]
    for (title, headers), rows in zip(SHEET_SPECS, all_rows):
        ws = wb.create_sheet(title=title)
        _write_sheet(ws, headers, rows)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _fetch_stripe_rows(
    session: Session,
    club_names: dict[int, str],
    from_dt: datetime,
    to_dt: datetime,
) -> list[list[Any]]:
    query = _apply_audit_stripe_filters(
        session.query(StripeCheckoutSession),
        from_dt=from_dt,
        to_dt=to_dt,
    )
    effective_dt = func.coalesce(
        StripeCheckoutSession.completed_at,
        StripeCheckoutSession.created_at,
    )
    rows = query.order_by(effective_dt.desc(), StripeCheckoutSession.id.desc()).all()

    customer_by_stripe_id: dict[str, StripeCustomer] = {}
    if rows:
        customer_ids = {row.stripe_customer_id for row in rows}
        for cust in (
            session.query(StripeCustomer)
            .filter(StripeCustomer.stripe_customer_id.in_(customer_ids))
            .all()
        ):
            customer_by_stripe_id[cust.stripe_customer_id] = cust

    out: list[list[Any]] = []
    for row in rows:
        cust = customer_by_stripe_id.get(row.stripe_customer_id)
        title, gg_id = resolve_group_title(
            session,
            row.telegram_chat_id,
            fallback_gg_player_id=cust.gg_player_id if cust else None,
        )
        method_name, _method_slug = resolve_method_display(
            session, row.club_id, row.payment_method_id
        )
        completed = row.completed_at or row.created_at
        out.append(
            [
                _fmt_dt(completed),
                _club_name(club_names, row.club_id),
                title or "",
                lookup_gg_nickname(session, row.club_id, gg_id) or "",
                gg_id or "",
                method_name or "",
                _fmt_decimal(row.amount_cents / 100),
                _fmt_decimal(_stripe_fee_usd(row.amount_cents)),
                row.currency or "",
                row.stripe_payment_intent_id or "",
                row.stripe_checkout_session_id,
            ]
        )
    return out


def _fetch_manual_rows(
    session: Session,
    club_names: dict[int, str],
    payment_cls,
    build_read: Callable,
    from_dt: datetime,
    to_dt: datetime,
    to_row: Callable[[dict, dict[int, str]], list[Any]],
) -> list[list[Any]]:
    query = _apply_audit_manual_filters(
        session,
        session.query(payment_cls),
        payment_cls,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    rows = query.order_by(payment_cls.created_at.desc(), payment_cls.id.desc()).all()
    return [to_row(build_read(session, row), club_names) for row in rows]


def _venmo_row(data: dict, club_names: dict[int, str]) -> list[Any]:
    return [
        _fmt_dt(data["created_at"]),
        _club_name(club_names, data.get("club_id")),
        data["payer_name"],
        data["venmo_handle"],
        data.get("group_title") or "",
        data.get("gg_nickname") or "",
        data.get("gg_player_id") or "",
        _fmt_decimal(data["amount_usd"]),
        data["status"],
        data["auto_bound"],
        data["goods_or_services"],
    ]


def _zelle_row(data: dict, club_names: dict[int, str]) -> list[Any]:
    return [
        _fmt_dt(data["created_at"]),
        _club_name(club_names, data.get("club_id")),
        data["payer_name"],
        data["zelle_recipient"],
        data.get("group_title") or "",
        data.get("gg_nickname") or "",
        data.get("gg_player_id") or "",
        _fmt_decimal(data["amount_usd"]),
        data["status"],
        data["auto_bound"],
    ]


def _cashapp_row(data: dict, club_names: dict[int, str]) -> list[Any]:
    return [
        _fmt_dt(data["created_at"]),
        _club_name(club_names, data.get("club_id")),
        data["payer_name"],
        data["cashapp_handle"],
        data.get("group_title") or "",
        data.get("gg_nickname") or "",
        data.get("gg_player_id") or "",
        _fmt_decimal(data["amount_usd"]),
        data["status"],
        data["auto_bound"],
    ]


def _paypal_row(data: dict, club_names: dict[int, str]) -> list[Any]:
    return [
        _fmt_dt(data["created_at"]),
        _club_name(club_names, data.get("club_id")),
        data["payer_name"],
        data["paypal_email"],
        data.get("group_title") or "",
        data.get("gg_nickname") or "",
        data.get("gg_player_id") or "",
        _fmt_decimal(data["amount_usd"]),
        data["status"],
        data["auto_bound"],
    ]


def _crypto_row(data: dict, club_names: dict[int, str]) -> list[Any]:
    return [
        _fmt_dt(data["created_at"]),
        _club_name(club_names, data.get("club_id")),
        data.get("from_label") or "",
        data["chain"],
        data["token_symbol"],
        data["to_address"],
        data["transaction_hash"],
        data.get("group_title") or "",
        data.get("gg_nickname") or "",
        data.get("gg_player_id") or "",
        _fmt_decimal(data["amount_usd"]),
        data["status"],
    ]
