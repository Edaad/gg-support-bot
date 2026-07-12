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
from api.audit_reconcile_matching import match_trade_lines_to_ledger
from api.club_audit_timezone import zone_for_slug

_HEADER_FILL = PatternFill("solid", fgColor="38761D")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_SECTION_FONT = Font(bold=True, size=12)
_TITLE_FONT = Font(bold=True, size=14)
_CURRENCY_FORMAT = "$#,##0.00"

# Intro: title (1), what (2), how (3), columns (4); blank spacer (5); tables from 6.
SHEET_INTRO_DATA_START_ROW = 6

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
    "Discrepancy",
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

MATCHING_HEADERS = [
    "Time",
    "Amount",
    "Player ID",
    "Nickname",
    "Name",
    "Source",
    "Time",
    "$",
    "Variant",
]

MATCHING_TRADE_HEADERS = [
    "Time",
    "Amount",
    "Player ID",
    "Nickname",
]

MATCHING_SUB_HEADERS = [
    "Name",
    "Source",
    "Time",
    "$",
]

# (what it shows, how it works, column glossary)
SHEET_INTROS: dict[str, tuple[str, str, str]] = {
    "Overview": (
        "Net ClubGG trade totals vs our internal ledger by player. "
        "The ledger is Round Table’s own record of chip/money movements for the "
        "audit day — not ClubGG’s trade export — built from deposits "
        "(Stripe, Zelle, Venmo, Cash App, PayPal, Crypto), early rakeback, "
        "bonuses, Monday RB settlement, and staff cashouts.",
        "For each player we sum trade lines and sum signed ledger events, then "
        "compare. Players within $2 are Matched (left); everyone else is "
        "Mismatched (right).",
        "Columns: Nickname — ClubGG nick; Player ID — GG player id; "
        "Net Trade Record — sum of ClubGG trade lines for the day; "
        "Net Ledger — sum of signed internal ledger events for the day "
        "(deposits/early RB/bonuses/Monday settlement as club outflows; "
        "cashouts as club inflows).",
    ),
    "Details": (
        "Same players as Overview, with the internal ledger broken into "
        "components (deposits, early RB, bonuses, Monday settlement, cashouts) "
        "alongside ClubGG net trade.",
        "Mismatched first, then Matched. Discrepancy is net trade − net ledger "
        "(how far ClubGG and our ledger disagree for that player).",
        "Columns: Nickname; Player ID; Deposits — signed total of payment "
        "deposits in our ledger; Early RB — early rakeback issued; Bonuses; "
        "RB settlement (Monday) — weekly settlement from gg-computer when "
        "applicable; Cashouts — staff cashouts in our ledger; "
        "Net Trade Record — ClubGG sum; Net Ledger — sum of those ledger "
        "parts; Discrepancy — net trade − net ledger.",
    ),
    "Net Ledger": (
        "Line-by-line view of our internal ledger for the audit day — every "
        "deposit, early RB, bonus, Monday settlement, and cashout event we "
        "recorded (the same events that roll up into Net Ledger on Overview).",
        "Amounts use club chip-ledger signs: money/chips we send to the player "
        "(deposits, RB, bonuses, Monday settlement) are negative; cashouts "
        "(player returning chips) are positive — matching ClubGG trade signs.",
        "Columns: Player ID; Nickname; Source — ledger event type "
        "(Stripe, Zelle, Bonus, Cashout, etc.); Amount — signed USD; "
        "Time — club-local; Reference — external id / detail from our system.",
    ),
    "Deposits": (
        "Deposit subset of our internal ledger only (payment methods), "
        "grouped by provider. Early RB, bonuses, Monday settlement, and "
        "cashouts are not listed here.",
        "Sections in Stripe → Zelle → Venmo → Cash App → PayPal → Crypto order.",
        "Columns: Player ID; Nickname; Amount — signed USD (club outflow); "
        "Group / detail — group title or note; Time — club-local; "
        "Reference — external payment id in our DB.",
    ),
    "Matching": (
        "One row per ClubGG trade-record line with a best-effort suggestion "
        "from our internal ledger (same deposit/RB/bonus/cashout events as "
        "Net Ledger).",
        "Same player preferred, whole-dollar rounding, ±15 minutes, sign-aware; "
        "each ledger event used once. Variant holds account tags "
        "(Zelle recipient, Venmo/Cash App handle, PayPal email, crypto token) "
        "or bonus type when matched.",
        "Columns: Time / Amount / Player ID / Nickname — ClubGG trade line; "
        "Best effort match — Name (payer/nick/id), Source, Time, $; "
        "Variant — payment account tag or bonus type, blank when none.",
    ),
}

OVERVIEW_CURRENCY_COLS = (3, 4)
DETAIL_CURRENCY_COLS = (3, 4, 5, 6, 7, 8, 9, 10)
OVERVIEW_RIGHT_CURRENCY_COLS = (8, 9)

OVERVIEW_WIDTHS = [22, 16, 18, 18, 3, 22, 16, 18, 18]
DETAIL_WIDTHS = [22, 16, 14, 14, 14, 22, 14, 18, 18, 16]
NET_LEDGER_WIDTHS = [16, 22, 18, 14, 18, 40]
DEPOSIT_WIDTHS = [16, 22, 14, 40, 18, 28]
MATCHING_WIDTHS = [18, 12, 16, 22, 22, 14, 18, 10, 22]


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


def _style_header_row(
    ws: Worksheet,
    row: int,
    headers: list[str],
    *,
    start_col: int = 1,
) -> None:
    for offset, header in enumerate(headers):
        cell = ws.cell(row=row, column=start_col + offset, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="left", vertical="center")


def _style_section_title(
    ws: Worksheet,
    row: int,
    title: str,
    *,
    col: int = 1,
) -> None:
    cell = ws.cell(row=row, column=col, value=title)
    cell.font = _SECTION_FONT


def _write_sheet_intro(
    ws: Worksheet,
    title: str,
    *,
    merge_cols: int = 6,
) -> int:
    """Write title / what / how / columns at rows 1–4. Returns first data row."""
    what, how, columns = SHEET_INTROS[title]
    title_cell = ws.cell(row=1, column=1, value=title)
    title_cell.font = _TITLE_FONT

    what_cell = ws.cell(row=2, column=1, value=what)
    what_cell.alignment = Alignment(wrap_text=True, vertical="top")

    how_cell = ws.cell(row=3, column=1, value=how)
    how_cell.alignment = Alignment(wrap_text=True, vertical="top")

    columns_cell = ws.cell(row=4, column=1, value=columns)
    columns_cell.alignment = Alignment(wrap_text=True, vertical="top")

    if merge_cols > 1:
        end_letter = ws.cell(row=1, column=merge_cols).column_letter
        ws.merge_cells(f"A2:{end_letter}2")
        ws.merge_cells(f"A3:{end_letter}3")
        ws.merge_cells(f"A4:{end_letter}4")

    ws.row_dimensions[2].height = 48
    ws.row_dimensions[3].height = 48
    ws.row_dimensions[4].height = 72
    return SHEET_INTRO_DATA_START_ROW


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


def _write_overview_sheet(
    ws: Worksheet,
    *,
    matched: list[AuditReconcilePlayerResult],
    mismatched: list[AuditReconcilePlayerResult],
) -> None:
    start = _write_sheet_intro(ws, "Overview", merge_cols=9)
    left_col = 1
    right_col = 6

    _style_section_title(ws, start, "Matched", col=left_col)
    _style_section_title(ws, start, "Mismatched", col=right_col)

    header_row = start + 1
    _style_header_row(ws, header_row, OVERVIEW_HEADERS, start_col=left_col)
    _style_header_row(ws, header_row, OVERVIEW_HEADERS, start_col=right_col)

    data_start = header_row + 1
    row_count = max(len(matched), len(mismatched))
    for i in range(row_count):
        row = data_start + i
        if i < len(matched):
            for offset, value in enumerate(_overview_row(matched[i])):
                ws.cell(row=row, column=left_col + offset, value=value)
        if i < len(mismatched):
            for offset, value in enumerate(_overview_row(mismatched[i])):
                ws.cell(row=row, column=right_col + offset, value=value)

    if row_count:
        data_end = data_start + row_count - 1
        _format_currency_cells(ws, data_start, data_end, OVERVIEW_CURRENCY_COLS)
        _format_currency_cells(ws, data_start, data_end, OVERVIEW_RIGHT_CURRENCY_COLS)


def _write_player_sections(
    ws: Worksheet,
    *,
    headers: list[str],
    row_builder,
    currency_cols: tuple[int, ...],
    matched: list[AuditReconcilePlayerResult],
    mismatched: list[AuditReconcilePlayerResult],
    start_row: int = 1,
    mismatch_first: bool = False,
) -> None:
    row = start_row
    if mismatch_first:
        sections = (
            ("Mismatched", mismatched),
            ("Matched", matched),
        )
    else:
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


def _write_details_sheet(
    ws: Worksheet,
    *,
    matched: list[AuditReconcilePlayerResult],
    mismatched: list[AuditReconcilePlayerResult],
) -> None:
    start = _write_sheet_intro(ws, "Details", merge_cols=10)
    _write_player_sections(
        ws,
        headers=DETAIL_HEADERS,
        row_builder=_detail_row,
        currency_cols=DETAIL_CURRENCY_COLS,
        matched=matched,
        mismatched=mismatched,
        start_row=start,
        mismatch_first=True,
    )


def _write_net_ledger_sheet(
    ws: Worksheet,
    report: AuditReconcileReport,
) -> None:
    start = _write_sheet_intro(ws, "Net Ledger", merge_cols=6)
    _style_header_row(ws, start, NET_LEDGER_HEADERS)
    row_idx = start + 1
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
    start = _write_sheet_intro(ws, "Deposits", merge_cols=6)
    by_method = deposit_lines_by_method(report.ledger_lines)
    row_idx = start
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


def _write_matching_sheet(
    ws: Worksheet,
    report: AuditReconcileReport,
) -> None:
    start = _write_sheet_intro(ws, "Matching", merge_cols=9)
    group_row = start
    sub_row = start + 1

    # Trade columns + Variant: vertically merge across the two header rows.
    for col, header in enumerate(MATCHING_TRADE_HEADERS, start=1):
        cell = ws.cell(row=group_row, column=col, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="left", vertical="center")
        ws.merge_cells(
            start_row=group_row,
            start_column=col,
            end_row=sub_row,
            end_column=col,
        )

    group_cell = ws.cell(row=group_row, column=5, value="Best effort match")
    group_cell.fill = _HEADER_FILL
    group_cell.font = _HEADER_FONT
    group_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells(start_row=group_row, start_column=5, end_row=group_row, end_column=8)

    for offset, header in enumerate(MATCHING_SUB_HEADERS):
        cell = ws.cell(row=sub_row, column=5 + offset, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="left", vertical="center")

    variant_cell = ws.cell(row=group_row, column=9, value="Variant")
    variant_cell.fill = _HEADER_FILL
    variant_cell.font = _HEADER_FONT
    variant_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.merge_cells(
        start_row=group_row,
        start_column=9,
        end_row=sub_row,
        end_column=9,
    )

    matched_rows = match_trade_lines_to_ledger(
        report.trade_lines,
        report.ledger_lines,
        club_slug=report.club_slug,
    )
    row_idx = sub_row + 1
    for matched in matched_rows:
        trade = matched.trade
        ws.cell(
            row=row_idx,
            column=1,
            value=_format_time(report.club_slug, trade.occurred_at),
        )
        cell = ws.cell(
            row=row_idx,
            column=2,
            value=_decimal_cell(trade.amount),
        )
        cell.number_format = _CURRENCY_FORMAT
        ws.cell(row=row_idx, column=3, value=trade.member_gg_player_id or "")
        ws.cell(row=row_idx, column=4, value=trade.member_nickname or "")
        ws.cell(row=row_idx, column=5, value=matched.match_name)
        ws.cell(row=row_idx, column=6, value=matched.match_source)
        ws.cell(row=row_idx, column=7, value=matched.match_time)
        ws.cell(row=row_idx, column=8, value=matched.match_amount)
        ws.cell(row=row_idx, column=9, value=matched.variant)
        row_idx += 1


def build_reconcile_workbook_from_report(report: AuditReconcileReport) -> bytes:
    matched, mismatched = _partition_players(report.players)

    wb = Workbook()
    overview = wb.active
    overview.title = "Overview"
    details = wb.create_sheet("Details")
    net_ledger = wb.create_sheet("Net Ledger")
    deposits = wb.create_sheet("Deposits")
    matching = wb.create_sheet("Matching")

    _write_overview_sheet(overview, matched=matched, mismatched=mismatched)
    _write_details_sheet(details, matched=matched, mismatched=mismatched)
    _write_net_ledger_sheet(net_ledger, report)
    _write_deposits_sheet(deposits, report)
    _write_matching_sheet(matching, report)

    _set_column_widths(overview, OVERVIEW_WIDTHS)
    _set_column_widths(details, DETAIL_WIDTHS)
    _set_column_widths(net_ledger, NET_LEDGER_WIDTHS)
    _set_column_widths(deposits, DEPOSIT_WIDTHS)
    _set_column_widths(matching, MATCHING_WIDTHS)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
