"""Cross-club audit export: one XLSX workbook with a sheet per payment provider."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.payments_helpers import (
    apply_analytics_payment_exclusion,
    build_cashapp_payment_read,
    build_paypal_payment_read,
    build_venmo_payment_read,
    build_zelle_payment_read,
    lookup_gg_nickname,
    resolve_group_title,
)
from config import CLUB_SHORTHAND_TO_NAME
from db.models import (
    CashAppPayment,
    Club,
    PayPalPayment,
    StripeCheckoutSession,
    StripeCustomer,
    VenmoPayment,
    ZellePayment,
)

STRIPE_LAYOUT = ["Amount", "Player", "Time"]
MANUAL_LAYOUT = ["Amount", "Name", "Club", "Time"]

SheetLayout = Literal["stripe", "manual"]

_EASTERN = ZoneInfo("America/New_York")
_HEADER_FILL = PatternFill("solid", fgColor="38761D")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_CURRENCY_FORMAT = "$#,##0.00"
_COLUMN_WIDTHS: dict[SheetLayout, list[float]] = {
    "stripe": [14, 40, 28],
    "manual": [14, 28, 18, 28],
}


@dataclass(frozen=True)
class SheetSpec:
    title: str
    headers: list[str]
    layout: SheetLayout


SHEET_SPECS: list[SheetSpec] = [
    SheetSpec("Stripe", STRIPE_LAYOUT, "stripe"),
    SheetSpec("Zelle", MANUAL_LAYOUT, "manual"),
    SheetSpec("venmo", MANUAL_LAYOUT, "manual"),
    SheetSpec("cashapp", MANUAL_LAYOUT, "manual"),
    SheetSpec("PayPal", MANUAL_LAYOUT, "manual"),
    SheetSpec("bonus", MANUAL_LAYOUT, "manual"),
    SheetSpec("early rakeback", STRIPE_LAYOUT, "stripe"),
]


@dataclass(frozen=True)
class StripeAuditRow:
    amount_usd: float
    player: str
    time_label: str
    stripe_fee_usd: Decimal


@dataclass(frozen=True)
class ManualAuditRow:
    amount_usd: float
    payer_name: str
    club_label: str
    time_label: str


def _stripe_fee_usd(amount_cents: int) -> Decimal:
    return Decimal(round(amount_cents * 0.029 + 30)) / Decimal(100)


def _to_eastern(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.astimezone(_EASTERN)


def _ordinal_day(day: int) -> str:
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def _fmt_stripe_audit_time(value: datetime | None) -> str:
    if value is None:
        return ""
    dt = _to_eastern(value)
    month = dt.strftime("%b")
    day = _ordinal_day(dt.day)
    year = dt.year
    clock = dt.strftime("%I:%M %p").lstrip("0")
    return f"{month} {day} {year}, {clock}"


def _fmt_manual_audit_time(value: datetime | None) -> str:
    if value is None:
        return ""
    dt = _to_eastern(value)
    month = dt.strftime("%B")
    day = dt.day
    year = dt.year
    clock = dt.strftime("%I:%M %p").lstrip("0")
    return f"{month} {day}, {year} at {clock}"


def _shorthand_for_club_name(club_name: str) -> str:
    lower = (club_name or "").strip().lower()
    if lower == "clubgto":
        return "GTO"
    if lower == "creator club":
        return "CC"
    if lower == "round table":
        return "RT"
    for shorthand, full_name in CLUB_SHORTHAND_TO_NAME.items():
        if full_name.lower() == lower:
            return shorthand
    return (club_name or "").strip()


def _stripe_player_cell(
    *,
    group_title: str | None,
    club_name: str,
    gg_player_id: str | None,
    gg_nickname: str | None,
) -> str:
    title = (group_title or "").strip()
    if title:
        return title
    shorthand = _shorthand_for_club_name(club_name)
    player_id = (gg_player_id or "").strip()
    nickname = (gg_nickname or "").strip()
    parts = [part for part in (shorthand, player_id, nickname) if part]
    return " / ".join(parts)


def _manual_club_cell(data: dict, club_key: str) -> str:
    return str(data.get(club_key) or "").strip()


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


def _write_formatted_sheet(
    ws: Worksheet,
    spec: SheetSpec,
    stripe_rows: list[StripeAuditRow] | None = None,
    manual_rows: list[ManualAuditRow] | None = None,
) -> None:
    ws.append(spec.headers)
    for col_idx, header in enumerate(spec.headers, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="left", vertical="center")

    data_row_start = 2
    if spec.layout == "stripe":
        rows = stripe_rows or []
        for row_idx, row in enumerate(rows, start=data_row_start):
            ws.append([row.amount_usd, row.player, row.time_label])
            amount_cell = ws.cell(row=row_idx, column=1)
            amount_cell.number_format = _CURRENCY_FORMAT
            amount_cell.alignment = Alignment(horizontal="right")
            fee = row.stripe_fee_usd
            amount_cell.comment = Comment(
                f"Stripe fee: ${fee:.2f}",
                "audit-export",
            )
    else:
        rows = manual_rows or []
        for row_idx, row in enumerate(rows, start=data_row_start):
            ws.append([row.amount_usd, row.payer_name, row.club_label, row.time_label])
            amount_cell = ws.cell(row=row_idx, column=1)
            amount_cell.number_format = _CURRENCY_FORMAT
            amount_cell.alignment = Alignment(horizontal="right")

    for col_idx, width in enumerate(_COLUMN_WIDTHS[spec.layout], start=1):
        letter = ws.cell(row=1, column=col_idx).column_letter
        ws.column_dimensions[letter].width = width

    if spec.headers:
        ws.auto_filter.ref = ws.dimensions


def build_audit_workbook(session: Session, from_dt: datetime, to_dt: datetime) -> bytes:
    club_names = _club_name_map(session)
    wb = Workbook()
    wb.remove(wb.active)

    stripe_rows = _fetch_stripe_rows(session, club_names, from_dt, to_dt)
    zelle_rows = _fetch_manual_rows(
        session,
        ZellePayment,
        build_zelle_payment_read,
        from_dt,
        to_dt,
        lambda data: _manual_row(data, "zelle_recipient"),
    )
    venmo_rows = _fetch_manual_rows(
        session,
        VenmoPayment,
        build_venmo_payment_read,
        from_dt,
        to_dt,
        lambda data: _manual_row(data, "venmo_handle"),
    )
    cashapp_rows = _fetch_manual_rows(
        session,
        CashAppPayment,
        build_cashapp_payment_read,
        from_dt,
        to_dt,
        lambda data: _manual_row(data, "cashapp_handle"),
    )
    paypal_rows = _fetch_manual_rows(
        session,
        PayPalPayment,
        build_paypal_payment_read,
        from_dt,
        to_dt,
        lambda data: _manual_row(data, "paypal_email"),
    )

    sheet_rows: list[list[StripeAuditRow] | list[ManualAuditRow]] = [
        stripe_rows,
        zelle_rows,
        venmo_rows,
        cashapp_rows,
        paypal_rows,
        [],
        [],
    ]

    for spec, rows in zip(SHEET_SPECS, sheet_rows):
        ws = wb.create_sheet(title=spec.title)
        if spec.layout == "stripe":
            _write_formatted_sheet(ws, spec, stripe_rows=rows)  # type: ignore[arg-type]
        else:
            _write_formatted_sheet(ws, spec, manual_rows=rows)  # type: ignore[arg-type]

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _fetch_stripe_rows(
    session: Session,
    club_names: dict[int, str],
    from_dt: datetime,
    to_dt: datetime,
) -> list[StripeAuditRow]:
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

    out: list[StripeAuditRow] = []
    for row in rows:
        cust = customer_by_stripe_id.get(row.stripe_customer_id)
        title, gg_id = resolve_group_title(
            session,
            row.telegram_chat_id,
            fallback_gg_player_id=cust.gg_player_id if cust else None,
        )
        nickname = lookup_gg_nickname(session, row.club_id, gg_id) or ""
        completed = row.completed_at or row.created_at
        out.append(
            StripeAuditRow(
                amount_usd=float(row.amount_cents) / 100.0,
                player=_stripe_player_cell(
                    group_title=title,
                    club_name=_club_name(club_names, row.club_id),
                    gg_player_id=gg_id,
                    gg_nickname=nickname,
                ),
                time_label=_fmt_stripe_audit_time(completed),
                stripe_fee_usd=_stripe_fee_usd(row.amount_cents),
            )
        )
    return out


def _fetch_manual_rows(
    session: Session,
    payment_cls,
    build_read: Callable,
    from_dt: datetime,
    to_dt: datetime,
    to_row: Callable[[dict], ManualAuditRow],
) -> list[ManualAuditRow]:
    query = _apply_audit_manual_filters(
        session,
        session.query(payment_cls),
        payment_cls,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    rows = query.order_by(payment_cls.created_at.desc(), payment_cls.id.desc()).all()
    return [to_row(build_read(session, row)) for row in rows]


def _manual_row(data: dict, club_key: str) -> ManualAuditRow:
    amount = data["amount_usd"]
    if isinstance(amount, Decimal):
        amount_usd = float(amount)
    else:
        amount_usd = float(amount)
    return ManualAuditRow(
        amount_usd=amount_usd,
        payer_name=str(data["payer_name"]),
        club_label=_manual_club_cell(data, club_key),
        time_label=_fmt_manual_audit_time(data["created_at"]),
    )
