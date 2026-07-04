"""XLSX export for audit reconcile runs."""

from __future__ import annotations

import io
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from api.audit_reconcile import AuditReconcileReport

_HEADER_FILL = PatternFill("solid", fgColor="38761D")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_CURRENCY_FORMAT = "$#,##0.00"

RECONCILE_HEADERS = [
    "Player ID",
    "Net Trade Record",
    "Deposits",
    "Early RB",
    "Bonuses",
    "Monday",
    "Glide",
    "Cashouts",
    "Net Ledger",
    "Delta",
    "Status",
]


def _decimal_cell(value: Decimal) -> float:
    return float(value)


def build_reconcile_workbook_from_report(report: AuditReconcileReport) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Reconcile"
    ws.append(RECONCILE_HEADERS)
    for col_idx in range(1, len(RECONCILE_HEADERS) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="left", vertical="center")

    for player in report.players:
        bd = player.ledger_breakdown
        ws.append(
            [
                player.gg_player_id,
                _decimal_cell(player.net_trade_record),
                _decimal_cell(bd.deposits),
                _decimal_cell(bd.early_rb),
                _decimal_cell(bd.bonuses),
                _decimal_cell(bd.monday),
                _decimal_cell(bd.glide),
                _decimal_cell(bd.cashouts),
                _decimal_cell(player.net_ledger),
                _decimal_cell(player.delta),
                player.status,
            ]
        )

    for row_idx in range(2, ws.max_row + 1):
        for col_idx in range(2, 10):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.number_format = _CURRENCY_FORMAT
            cell.alignment = Alignment(horizontal="right")

    widths = [16, 16, 14, 14, 14, 14, 14, 14, 14, 14, 14]
    for col_idx, width in enumerate(widths, start=1):
        letter = ws.cell(row=1, column=col_idx).column_letter
        ws.column_dimensions[letter].width = width

    if ws.max_row >= 1:
        ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
