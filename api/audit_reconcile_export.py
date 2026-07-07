"""XLSX export for audit reconcile runs."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from api.audit_ledger import (
    DEPOSIT_METHOD_ORDER,
    LEDGER_SOURCE_LABELS,
    LedgerLine,
)
from api.audit_reconcile import AuditReconcilePlayerResult, AuditReconcileReport
from api.club_audit_timezone import zone_for_slug

_HEADER_FILL = PatternFill("solid", fgColor="38761D")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_SECTION_FONT = Font(bold=True, size=12)
_CURRENCY_FORMAT = "$#,##0.00"

OVERVIEW_HEADERS = [
    "Nickname",
    "Player ID",
    "Net Trade Record",
    "Net Ledger",
]

DETAIL_HEADERS = [
    "Nickname",
    "Player ID",
    "Deposits",
    "Early RB",
    "Bonuses",
    "RB settlement (Monday)",
    "Cashouts",
    "Net Trade Record",
    "Net Ledger",
    "Delta",
]

NET_LEDGER_HEADERS = [
    "Player ID",
    "Nickname",
    "Source",
    "Amount",
    "Time",
    "Reference",
]

DEPOSIT_HEADERS = [
    "Player ID",
    "Nickname",
    "Amount",
    "Group / detail",
    "Time",
    "Reference",
]

OVERVIEW_CURRENCY_COLS = (3, 4)
DETAIL_CURRENCY_COLS = (3, 4, 5, 6, 7, 8, 9, 10)

OVERVIEW_WIDTHS = [22, 16, 18, 18]
DETAIL_WIDTHS = [22, 16, 14, 14, 14, 22, 14, 18, 18, 14]


def _decimal_cell(value: Decimal) -> float:
    return float(value)


def _partition_players(
    players: list[AuditReconcilePlayerResult],
) -> tuple[list[AuditReconcilePlayerResult], list[AuditReconcilePlayerResult]]:
    matched = [p for p in players if p.status == "match"]
    mismatched = [p for p in players if p.status != "match"]
    matched.sort(key=lambda p: ((p.member_nickname or "").lower(), p.gg_player_id))
    mismatched.sort(
        key=lambda p: (-abs(p.delta), (p.member_nickname or "").lower(), p.gg_player_id)
    )
    return matched, mismatched


def _style_header_row(ws: Worksheet, row: int, headers: list[str]) -> None:
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col_idx, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="left", vertical="center")


def _style_section_title(ws: Worksheet, row: int, title: str) -> None:
    cell = ws.cell(row=row, column=1, value=title)
    cell.font = _SECTION_FONT


def _format_currency_cells(
    ws: Worksheet,
    row_start: int,
    row_end: int,
    currency_cols: tuple[int, ...],
) -> None:
    for row_idx in range(row_start, row_end + 1):
        for col_idx in currency_cols:
            cell = ws.cell(row=row_idx, column=col_idx)
            if isinstance(cell.value, (int, float)):
                cell.number_format = _CURRENCY_FORMAT
                cell.alignment = Alignment(horizontal="right")


def _set_column_widths(ws: Worksheet, widths: list[int]) -> None:
    for col_idx, width in enumerate(widths, start=1):
        letter = ws.cell(row=1, column=col_idx).column_letter
        ws.column_dimensions[letter].width = width


def _format_time(club_slug: str, occurred_at: datetime | None) -> str:
    if occurred_at is None:
        return ""
    dt = occurred_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    local = dt.astimezone(zone_for_slug(club_slug))
    return local.strftime("%Y-%m-%d %H:%M")


def _reference_text(line: LedgerLine) -> str:
    parts: list[str] = []
    if line.external_id:
        parts.append(line.external_id)
    if line.detail:
        parts.append(line.detail)
    return " — ".join(parts)


def deposit_lines_by_method(
    lines: list[LedgerLine],
) -> dict[str, list[LedgerLine]]:
    by_method: dict[str, list[LedgerLine]] = {m: [] for m in DEPOSIT_METHOD_ORDER}
    for line in lines:
        if line.source.startswith("deposit_"):
            by_method.setdefault(line.source, []).append(line)
    return by_method


def _sort_net_ledger_lines(lines: list[LedgerLine]) -> list[LedgerLine]:
    def sort_key(line: LedgerLine) -> tuple:
        unmatched = 0 if line.gg_player_id else 1
        return (
            unmatched,
            line.gg_player_id or "",
            line.source,
            line.occurred_at_utc or datetime.min,
        )

    return sorted(lines, key=sort_key)


def _sort_deposit_lines(lines: list[LedgerLine]) -> list[LedgerLine]:
    return sorted(
        lines,
        key=lambda line: (
            line.occurred_at_utc or datetime.min,
            line.gg_player_id or "",
        ),
    )


def _overview_row(player: AuditReconcilePlayerResult) -> list[str | float]:
    return [
        player.member_nickname or "",
        player.gg_player_id,
        _decimal_cell(player.net_trade_record),
        _decimal_cell(player.net_ledger),
    ]


def _detail_row(player: AuditReconcilePlayerResult) -> list[str | float]:
    bd = player.ledger_breakdown
    return [
        player.member_nickname or "",
        player.gg_player_id,
        _decimal_cell(bd.deposits),
        _decimal_cell(bd.early_rb),
        _decimal_cell(bd.bonuses),
        _decimal_cell(bd.monday),
        _decimal_cell(bd.cashouts),
        _decimal_cell(player.net_trade_record),
        _decimal_cell(player.net_ledger),
        _decimal_cell(player.delta),
    ]


def _write_player_sections(
    ws: Worksheet,
    *,
    headers: list[str],
    row_builder,
    currency_cols: tuple[int, ...],
    matched: list[AuditReconcilePlayerResult],
    mismatched: list[AuditReconcilePlayerResult],
) -> None:
    row = 1
    sections = (
        ("Matched", matched),
        ("Mismatched", mismatched),
    )
    for section_idx, (title, players) in enumerate(sections):
        if section_idx > 0:
            row += 1
        _style_section_title(ws, row, title)
        row += 1
        _style_header_row(ws, row, headers)
        data_start = row + 1
        row = data_start
        for player in players:
            for col_idx, value in enumerate(row_builder(player), start=1):
                ws.cell(row=row, column=col_idx, value=value)
            row += 1
        if players:
            _format_currency_cells(ws, data_start, row - 1, currency_cols)


def _write_net_ledger_sheet(
    ws: Worksheet,
    report: AuditReconcileReport,
) -> None:
    _style_header_row(ws, 1, NET_LEDGER_HEADERS)
    row_idx = 2
    for line in _sort_net_ledger_lines(report.ledger_lines):
        ws.cell(row=row_idx, column=1, value=line.gg_player_id or "")
        ws.cell(row=row_idx, column=2, value=line.member_nickname or "")
        ws.cell(row=row_idx, column=3, value=line.source_label)
        cell = ws.cell(
            row=row_idx,
            column=4,
            value=_decimal_cell(line.amount_signed),
        )
        cell.number_format = _CURRENCY_FORMAT
        ws.cell(
            row=row_idx,
            column=5,
            value=_format_time(report.club_slug, line.occurred_at_utc),
        )
        ws.cell(row=row_idx, column=6, value=_reference_text(line))
        row_idx += 1


def _write_deposits_sheet(
    ws: Worksheet,
    report: AuditReconcileReport,
) -> None:
    by_method = deposit_lines_by_method(report.ledger_lines)
    row_idx = 1
    for method in DEPOSIT_METHOD_ORDER:
        method_lines = _sort_deposit_lines(by_method.get(method, []))
        if not method_lines:
            continue
        label = LEDGER_SOURCE_LABELS.get(method, method)
        ws.cell(row=row_idx, column=1, value=label).font = _SECTION_FONT
        row_idx += 1
        _style_header_row(ws, row_idx, DEPOSIT_HEADERS)
        row_idx += 1
        for line in method_lines:
            ws.cell(row=row_idx, column=1, value=line.gg_player_id or "")
            ws.cell(row=row_idx, column=2, value=line.member_nickname or "")
            cell = ws.cell(
                row=row_idx,
                column=3,
                value=_decimal_cell(line.amount_signed),
            )
            cell.number_format = _CURRENCY_FORMAT
            ws.cell(row=row_idx, column=4, value=line.detail or "")
            ws.cell(
                row=row_idx,
                column=5,
                value=_format_time(report.club_slug, line.occurred_at_utc),
            )
            ws.cell(row=row_idx, column=6, value=line.external_id)
            row_idx += 1
        row_idx += 1


def build_reconcile_workbook_from_report(report: AuditReconcileReport) -> bytes:
    matched, mismatched = _partition_players(report.players)

    wb = Workbook()
    overview = wb.active
    overview.title = "Overview"
    details = wb.create_sheet("Details")
    net_ledger = wb.create_sheet("Net Ledger")
    deposits = wb.create_sheet("Deposits")

    _write_player_sections(
        overview,
        headers=OVERVIEW_HEADERS,
        row_builder=_overview_row,
        currency_cols=OVERVIEW_CURRENCY_COLS,
        matched=matched,
        mismatched=mismatched,
    )
    _write_player_sections(
        details,
        headers=DETAIL_HEADERS,
        row_builder=_detail_row,
        currency_cols=DETAIL_CURRENCY_COLS,
        matched=matched,
        mismatched=mismatched,
    )
    _write_net_ledger_sheet(net_ledger, report)
    _write_deposits_sheet(deposits, report)

    _set_column_widths(overview, OVERVIEW_WIDTHS)
    _set_column_widths(details, DETAIL_WIDTHS)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
