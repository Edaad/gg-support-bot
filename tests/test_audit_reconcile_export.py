"""Tests for reconcile XLSX export."""

from __future__ import annotations

import io
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from openpyxl import load_workbook

from api.audit_ledger import LedgerBreakdown, LedgerLine
from api.audit_reconcile import AuditReconcilePlayerResult, AuditReconcileReport, TradeLineForMatch
from api.audit_reconcile_export import (
    DETAIL_HEADERS,
    MATCHING_HEADERS,
    MATCHING_WIDTHS,
    OVERVIEW_HEADERS,
    SHEET_INTRO_DATA_START_ROW,
    UNRESOLVED_HEADERS,
    _CURRENCY_FORMAT,
    _EXCEL_TIME_FORMAT,
    _MATCHING_BAND_FILL,
    _MATCHING_BODY_FONT,
    _MATCHING_HEADER_FONT,
    _MATCHING_ROW_HEIGHT,
    build_all_clubs_matching_workbook,
    build_reconcile_workbook_from_report,
)


def _player(
    *,
    gg_player_id: str,
    nickname: str,
    net_trade: str,
    net_ledger: str,
    delta: str,
    status: str,
) -> AuditReconcilePlayerResult:
    return AuditReconcilePlayerResult(
        gg_player_id=gg_player_id,
        member_nickname=nickname,
        net_trade_record=Decimal(net_trade),
        net_ledger=Decimal(net_ledger),
        delta=Decimal(delta),
        ledger_breakdown=LedgerBreakdown(
            deposits=Decimal(net_ledger),
            early_rb=Decimal("0"),
            bonuses=Decimal("0"),
            monday=Decimal("0"),
            cashouts=Decimal("0"),
        ),
        status=status,
    )


def _empty_report(*, club_slug: str, club_name: str) -> AuditReconcileReport:
    return AuditReconcileReport(
        audit_date=date(2026, 7, 3),
        club_slug=club_slug,
        club_name=club_name,
        status="pass",
        players=[],
    )


class ReconcileExportTestCase(unittest.TestCase):
    def test_workbook_layout_and_intros(self):
        occurred = datetime(2026, 7, 3, 15, 30, tzinfo=timezone.utc)
        report = AuditReconcileReport(
            audit_date=date(2026, 7, 3),
            club_slug="aces-table",
            club_name="Aces Table",
            status="fail",
            players=[
                _player(
                    gg_player_id="3011-9668",
                    nickname="AcePlayer",
                    net_trade="100",
                    net_ledger="100",
                    delta="0",
                    status="match",
                ),
                _player(
                    gg_player_id="3011-9999",
                    nickname="BadPlayer",
                    net_trade="50",
                    net_ledger="40",
                    delta="10",
                    status="mismatch",
                ),
            ],
            ledger_lines=[
                LedgerLine(
                    gg_player_id="3011-9668",
                    member_nickname="AcePlayer",
                    source="deposit_stripe",
                    source_label="Stripe",
                    amount_signed=Decimal("-100"),
                    occurred_at_utc=occurred,
                    external_id="deposit_stripe:1",
                    detail=None,
                ),
                LedgerLine(
                    gg_player_id="3011-9668",
                    member_nickname="AcePlayer",
                    source="cashout",
                    source_label="Cashout",
                    amount_signed=Decimal("40"),
                    occurred_at_utc=occurred,
                    external_id="cashout:1",
                    detail=None,
                ),
                LedgerLine(
                    gg_player_id=None,
                    member_nickname=None,
                    source="deposit_zelle",
                    source_label="Zelle",
                    amount_signed=Decimal("-25"),
                    occurred_at_utc=occurred,
                    external_id="deposit_zelle:2",
                    detail="Unknown group",
                ),
            ],
        )

        wb = load_workbook(io.BytesIO(build_reconcile_workbook_from_report(report)))
        self.assertEqual(
            wb.sheetnames,
            ["Overview", "Details", "Net Ledger", "Deposits", "Matching", "Unresolved"],
        )

        overview = wb["Overview"]
        self.assertEqual(overview["A1"].value, "Overview")
        self.assertTrue(overview["A2"].value)
        self.assertTrue(overview["A3"].value)
        self.assertIn("Columns:", overview["A4"].value or "")
        self.assertIn("Net Trade Record", overview["A4"].value or "")
        self.assertIn("internal ledger", (overview["A2"].value or "").lower())
        self.assertIn("deposits", (overview["A2"].value or "").lower())
        section_row = SHEET_INTRO_DATA_START_ROW
        self.assertEqual(overview.cell(row=section_row, column=1).value, "Matched")
        self.assertEqual(overview.cell(row=section_row, column=6).value, "Mismatched")
        header_row = section_row + 1
        self.assertEqual(
            [overview.cell(row=header_row, column=c).value for c in range(1, 5)],
            OVERVIEW_HEADERS,
        )
        self.assertEqual(
            [overview.cell(row=header_row, column=c).value for c in range(6, 10)],
            OVERVIEW_HEADERS,
        )
        data_row = header_row + 1
        self.assertEqual(overview.cell(row=data_row, column=1).value, "AcePlayer")
        self.assertEqual(overview.cell(row=data_row, column=2).value, "3011-9668")
        self.assertEqual(overview.cell(row=data_row, column=6).value, "BadPlayer")
        self.assertEqual(overview.cell(row=data_row, column=7).value, "3011-9999")

        details = wb["Details"]
        self.assertEqual(details["A1"].value, "Details")
        self.assertIn("Discrepancy", details["A4"].value or "")
        self.assertEqual(
            details.cell(row=SHEET_INTRO_DATA_START_ROW, column=1).value,
            "Mismatched",
        )
        self.assertEqual(
            [
                details.cell(row=SHEET_INTRO_DATA_START_ROW + 1, column=c).value
                for c in range(1, 11)
            ],
            DETAIL_HEADERS,
        )

        matching = wb["Matching"]
        self.assertEqual(
            [matching.cell(row=1, column=c).value for c in range(1, 11)],
            MATCHING_HEADERS,
        )
        self.assertIsNone(matching.cell(row=2, column=1).value)
        self.assertNotEqual(matching.cell(row=1, column=1).value, "Matching")

    def test_matching_flat_headers_manager_and_time_format(self):
        occurred = datetime(2026, 7, 3, 15, 30, tzinfo=timezone.utc)
        report = AuditReconcileReport(
            audit_date=date(2026, 7, 3),
            club_slug="clubgto",
            club_name="ClubGTO",
            status="pass",
            players=[],
            trade_lines=[
                TradeLineForMatch(
                    line_id=1,
                    occurred_at=occurred,
                    amount=Decimal("-50"),
                    member_gg_player_id="1111-2222",
                    member_nickname="P1",
                    sheet_row=1,
                    manager_nickname="TrafficLight7",
                ),
            ],
            ledger_lines=[
                LedgerLine(
                    gg_player_id="1111-2222",
                    member_nickname="P1",
                    source="deposit_zelle",
                    source_label="Zelle",
                    amount_signed=Decimal("-50"),
                    occurred_at_utc=occurred,
                    external_id="deposit_zelle:1",
                    display_name="Payer",
                    variant="2133729202",
                ),
                LedgerLine(
                    gg_player_id="1111-2222",
                    member_nickname="P1",
                    source="deposit_venmo",
                    source_label="Venmo",
                    amount_signed=Decimal("-20"),
                    occurred_at_utc=occurred,
                    external_id="deposit_venmo:1",
                    variant="@janseashells",
                ),
            ],
        )
        wb = load_workbook(io.BytesIO(build_reconcile_workbook_from_report(report)))
        matching = wb["Matching"]
        self.assertEqual(matching.cell(row=1, column=1).value, "Trade Time")
        self.assertEqual(matching.cell(row=1, column=2).value, "Manager")
        self.assertEqual(matching.cell(row=2, column=2).value, "TrafficLight7")
        self.assertEqual(matching.cell(row=2, column=10).value, "2133729202")
        self.assertEqual(
            matching.cell(row=2, column=3).number_format,
            _CURRENCY_FORMAT,
        )
        self.assertIn("[Red]", _CURRENCY_FORMAT)
        self.assertIsInstance(matching.cell(row=2, column=1).value, datetime)
        self.assertEqual(matching.cell(row=2, column=1).number_format, _EXCEL_TIME_FORMAT)
        self.assertIsInstance(matching.cell(row=2, column=8).value, datetime)
        self.assertEqual(matching.cell(row=2, column=8).number_format, _EXCEL_TIME_FORMAT)
        self.assertEqual(matching.cell(row=2, column=6).value, "GTO Zelle")
        # Vaughn tally keys off Source = GTO …
        self.assertEqual(matching.cell(row=1, column=12).value, "Vaughn methods")
        self.assertEqual(matching.cell(row=2, column=12).value, "Method")
        self.assertEqual(matching.cell(row=3, column=12).value, "Zelle")
        self.assertEqual(matching.cell(row=3, column=13).value, "2133729202")
        self.assertEqual(matching.cell(row=2, column=9).value, 50.0)
        self.assertEqual(matching.cell(row=2, column=9).number_format, _CURRENCY_FORMAT)
        zelle_count = matching.cell(row=3, column=14).value
        zelle_total = matching.cell(row=3, column=15).value
        self.assertIsInstance(zelle_count, str)
        self.assertTrue(zelle_count.startswith("="))
        self.assertIn('COUNTIF($F:$F,"GTO Zelle")', zelle_count)
        self.assertIsInstance(zelle_total, str)
        self.assertIn('SUMIF($F:$F,"GTO Zelle",$I:$I)', zelle_total)
        self.assertEqual(matching.cell(row=4, column=12).value, "Venmo")
        self.assertEqual(matching.cell(row=5, column=12).value, "Crypto")
        self.assertEqual(matching.cell(row=6, column=12).value, "Stripe")
        self.assertEqual(matching.cell(row=7, column=12).value, "Total")
        self.assertTrue(str(matching.cell(row=7, column=14).value).startswith("=SUM("))
        self.assertIn("Matching_clubgto", matching.tables)
        self.assertEqual(matching.tables["Matching_clubgto"].ref, "A1:J2")
        style = matching.tables["Matching_clubgto"].tableStyleInfo
        self.assertEqual(style.name, "TableStyleLight1")
        self.assertFalse(style.showRowStripes)
        header_fill = matching.cell(row=1, column=1).fill.fgColor.rgb
        self.assertTrue(str(header_fill).endswith("306A54"))
        self.assertEqual(matching.cell(row=1, column=1).font.name, _MATCHING_HEADER_FONT.name)
        self.assertEqual(matching.cell(row=1, column=1).font.size, _MATCHING_HEADER_FONT.size)
        self.assertEqual(matching.cell(row=2, column=5).font.name, _MATCHING_BODY_FONT.name)
        self.assertEqual(matching.cell(row=2, column=5).font.size, _MATCHING_BODY_FONT.size)
        # First data row is not banded.
        self.assertNotEqual(
            (matching.cell(row=2, column=1).fill.fgColor or None)
            and matching.cell(row=2, column=1).fill.fgColor.rgb,
            _MATCHING_BAND_FILL.fgColor.rgb,
        )
        self.assertEqual(matching.row_dimensions[1].height, _MATCHING_ROW_HEIGHT)
        self.assertEqual(matching.row_dimensions[2].height, _MATCHING_ROW_HEIGHT)
        self.assertEqual(matching.column_dimensions["A"].width, MATCHING_WIDTHS[0])
        self.assertEqual(matching.column_dimensions["H"].width, MATCHING_WIDTHS[7])
        self.assertGreaterEqual(MATCHING_WIDTHS[0], 18)
        self.assertGreaterEqual(MATCHING_WIDTHS[7], 18)
        validations = list(matching.data_validations.dataValidation)
        self.assertEqual(len(validations), 2)
        source_dv = next(dv for dv in validations if "GTO Stripe" in (dv.formula1 or ""))
        self.assertIn("F2:F2", source_dv.sqref)
        variant_dv = next(dv for dv in validations if "INDIRECT" in (dv.formula1 or ""))
        self.assertIn("J2:J2", variant_dv.sqref)
        self.assertIn("MATCH(", variant_dv.formula1)
        self.assertIn("ADDRESS(", variant_dv.formula1)
        # Hidden per-source variant lists start at column 30
        self.assertEqual(matching.cell(row=1, column=30).value, "GTO Stripe")
        self.assertTrue(matching.column_dimensions["AD"].hidden)

    def test_unresolved_sheet_lists_unmatched_ledger(self):
        occurred = datetime(2026, 7, 3, 15, 30, tzinfo=timezone.utc)
        report = AuditReconcileReport(
            audit_date=date(2026, 7, 3),
            club_slug="clubgto",
            club_name="ClubGTO",
            status="pass",
            players=[],
            trade_lines=[
                TradeLineForMatch(
                    line_id=1,
                    occurred_at=occurred,
                    amount=Decimal("-50"),
                    member_gg_player_id="1111-2222",
                    member_nickname="P1",
                    sheet_row=1,
                    manager_nickname="Mgr",
                ),
            ],
            ledger_lines=[
                LedgerLine(
                    gg_player_id="1111-2222",
                    member_nickname="P1",
                    source="deposit_zelle",
                    source_label="Zelle",
                    amount_signed=Decimal("-50"),
                    occurred_at_utc=occurred,
                    external_id="deposit_zelle:1",
                    display_name="Matched Payer",
                    variant="2133729202",
                    detail="GTO / 1111-2222 / P1",
                ),
                LedgerLine(
                    gg_player_id="3333-4444",
                    member_nickname="LeftOver",
                    source="deposit_stripe",
                    source_label="Stripe",
                    amount_signed=Decimal("-22"),
                    occurred_at_utc=occurred,
                    external_id="deposit_stripe:9",
                    display_name="Charlie Kim",
                    detail="GTO / 3333-4444 / Charlie Kim",
                ),
            ],
        )
        wb = load_workbook(io.BytesIO(build_reconcile_workbook_from_report(report)))
        unresolved = wb["Unresolved"]
        self.assertEqual(
            [unresolved.cell(row=1, column=c).value for c in range(1, 7)],
            UNRESOLVED_HEADERS,
        )
        self.assertEqual(unresolved.cell(row=2, column=1).value, "Stripe")
        self.assertEqual(unresolved.cell(row=2, column=2).value, 22.0)
        self.assertEqual(unresolved.cell(row=2, column=3).value, "Charlie Kim")
        self.assertEqual(
            unresolved.cell(row=2, column=4).value,
            "GTO / 3333-4444 / Charlie Kim",
        )
        self.assertEqual(unresolved.cell(row=2, column=5).value, "ClubGTO")
        self.assertTrue(str(unresolved.cell(row=2, column=6).value).endswith("AM") or
                        str(unresolved.cell(row=2, column=6).value).endswith("PM"))
        self.assertIsNone(unresolved.cell(row=3, column=1).value)
        self.assertIn("Unresolved_clubgto", unresolved.tables)

    def test_matching_vaughn_tally_only_clubgto(self):
        report = _empty_report(club_slug="round-table", club_name="Round Table")
        report.ledger_lines = [
            LedgerLine(
                gg_player_id="1",
                member_nickname="P",
                source="deposit_zelle",
                source_label="Zelle",
                amount_signed=Decimal("-10"),
                occurred_at_utc=None,
                external_id="deposit_zelle:1",
                variant="2133729202",
            ),
        ]
        wb = load_workbook(io.BytesIO(build_reconcile_workbook_from_report(report)))
        matching = wb["Matching"]
        self.assertIsNone(matching.cell(row=1, column=12).value)
        self.assertIn("Matching_round_table", matching.tables)
        self.assertEqual(matching.tables["Matching_round_table"].ref, "A1:J1")

    def test_all_clubs_matching_workbook_sheet_order(self):
        reports = {
            "round-table": _empty_report(
                club_slug="round-table", club_name="Round Table"
            ),
            "clubgto": _empty_report(club_slug="clubgto", club_name="ClubGTO"),
            "creator-club": _empty_report(
                club_slug="creator-club", club_name="Creator Club"
            ),
        }
        wb = load_workbook(io.BytesIO(build_all_clubs_matching_workbook(reports)))
        self.assertEqual(
            wb.sheetnames,
            ["Round Table", "ClubGTO", "Creator Club", "Unresolved"],
        )
        for name in ("Round Table", "ClubGTO", "Creator Club"):
            self.assertEqual(wb[name].cell(row=1, column=1).value, "Trade Time")
            self.assertEqual(wb[name].cell(row=1, column=2).value, "Manager")
        self.assertEqual(wb["ClubGTO"].cell(row=1, column=12).value, "Vaughn methods")
        self.assertIsNone(wb["Round Table"].cell(row=1, column=12).value)
        self.assertIn("Matching_round_table", wb["Round Table"].tables)
        self.assertIn("Matching_clubgto", wb["ClubGTO"].tables)
        self.assertIn("Matching_creator_club", wb["Creator Club"].tables)
        unresolved = wb["Unresolved"]
        self.assertEqual(
            [unresolved.cell(row=1, column=c).value for c in range(1, 7)],
            UNRESOLVED_HEADERS,
        )
        self.assertIn("Unresolved_all", unresolved.tables)

    def test_all_clubs_unresolved_sorts_mixed_naive_aware_times(self):
        """Regression: export-all 500'd on naive vs aware occurred_at compare."""
        aware = datetime(2026, 7, 22, 4, 0, tzinfo=timezone.utc)
        naive = datetime(2026, 7, 22, 5, 0)  # naive
        reports = {
            "round-table": _empty_report(
                club_slug="round-table", club_name="Round Table"
            ),
            "clubgto": _empty_report(club_slug="clubgto", club_name="ClubGTO"),
            "creator-club": _empty_report(
                club_slug="creator-club", club_name="Creator Club"
            ),
        }
        reports["clubgto"].ledger_lines = [
            LedgerLine(
                gg_player_id="1",
                member_nickname="A",
                source="deposit_stripe",
                source_label="Stripe",
                amount_signed=Decimal("-10"),
                occurred_at_utc=aware,
                external_id="deposit_stripe:1",
                display_name="Aware",
            ),
        ]
        reports["round-table"].ledger_lines = [
            LedgerLine(
                gg_player_id="2",
                member_nickname="B",
                source="deposit_venmo",
                source_label="Venmo",
                amount_signed=Decimal("-20"),
                occurred_at_utc=naive,
                external_id="deposit_venmo:1",
                display_name="Naive",
            ),
        ]
        # Must not raise TypeError on sort.
        wb = load_workbook(io.BytesIO(build_all_clubs_matching_workbook(reports)))
        unresolved = wb["Unresolved"]
        self.assertEqual(unresolved.cell(row=2, column=3).value, "Aware")
        self.assertEqual(unresolved.cell(row=3, column=3).value, "Naive")


if __name__ == "__main__":
    unittest.main()
