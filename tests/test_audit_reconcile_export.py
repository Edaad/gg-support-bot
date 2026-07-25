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
    OVERVIEW_HEADERS,
    SHEET_INTRO_DATA_START_ROW,
    _EXCEL_TIME_FORMAT,
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
            ["Overview", "Details", "Net Ledger", "Deposits", "Matching"],
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
        self.assertIsInstance(matching.cell(row=2, column=1).value, datetime)
        self.assertEqual(matching.cell(row=2, column=1).number_format, _EXCEL_TIME_FORMAT)
        self.assertIsInstance(matching.cell(row=2, column=8).value, datetime)
        self.assertEqual(matching.cell(row=2, column=8).number_format, _EXCEL_TIME_FORMAT)
        # No Vaughn method column; tally on right for ClubGTO
        self.assertEqual(matching.cell(row=1, column=12).value, "Vaughn methods")
        self.assertEqual(matching.cell(row=2, column=12).value, "Method")
        self.assertEqual(matching.cell(row=3, column=12).value, "Zelle")
        self.assertEqual(matching.cell(row=3, column=15).value, 50.0)

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
        self.assertEqual(wb.sheetnames, ["Round Table", "ClubGTO", "Creator Club"])
        for name in wb.sheetnames:
            self.assertEqual(wb[name].cell(row=1, column=1).value, "Trade Time")
            self.assertEqual(wb[name].cell(row=1, column=2).value, "Manager")
        self.assertEqual(wb["ClubGTO"].cell(row=1, column=12).value, "Vaughn methods")
        self.assertIsNone(wb["Round Table"].cell(row=1, column=12).value)


if __name__ == "__main__":
    unittest.main()
