"""Tests for reconcile XLSX export."""

from __future__ import annotations

import io
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from openpyxl import load_workbook

from api.audit_ledger import LedgerBreakdown, LedgerLine
from api.audit_reconcile import AuditReconcilePlayerResult, AuditReconcileReport
from api.audit_reconcile_export import (
    DETAIL_HEADERS,
    OVERVIEW_HEADERS,
    SHEET_INTRO_DATA_START_ROW,
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
        self.assertIn("Discrepancy", DETAIL_HEADERS)
        self.assertNotIn("Delta", DETAIL_HEADERS)
        self.assertNotIn("Glide", DETAIL_HEADERS)
        # Mismatched data then blank spacer then Matched section
        self.assertEqual(
            details.cell(row=SHEET_INTRO_DATA_START_ROW + 2, column=1).value,
            "BadPlayer",
        )
        self.assertEqual(
            details.cell(row=SHEET_INTRO_DATA_START_ROW + 2, column=10).value,
            10.0,
        )
        self.assertEqual(
            details.cell(row=SHEET_INTRO_DATA_START_ROW + 4, column=1).value,
            "Matched",
        )
        self.assertEqual(
            details.cell(row=SHEET_INTRO_DATA_START_ROW + 6, column=1).value,
            "AcePlayer",
        )

        net_ledger = wb["Net Ledger"]
        self.assertEqual(net_ledger["A1"].value, "Net Ledger")
        self.assertEqual(
            net_ledger.cell(row=SHEET_INTRO_DATA_START_ROW, column=1).value,
            "Player ID",
        )
        # Cashout then Stripe after header (same sort as before, offset by intro)
        self.assertEqual(
            net_ledger.cell(row=SHEET_INTRO_DATA_START_ROW + 1, column=3).value,
            "Cashout",
        )
        self.assertEqual(
            net_ledger.cell(row=SHEET_INTRO_DATA_START_ROW + 2, column=3).value,
            "Stripe",
        )
        self.assertEqual(
            net_ledger.cell(row=SHEET_INTRO_DATA_START_ROW + 1, column=4).value,
            40.0,
        )

        deposits = wb["Deposits"]
        self.assertEqual(deposits["A1"].value, "Deposits")
        self.assertEqual(
            deposits.cell(row=SHEET_INTRO_DATA_START_ROW, column=1).value,
            "Stripe",
        )

        matching = wb["Matching"]
        self.assertEqual(matching["A1"].value, "Matching")
        self.assertIn("Best effort match", matching["A4"].value or "")
        self.assertEqual(
            matching.cell(row=SHEET_INTRO_DATA_START_ROW, column=1).value,
            "Time",
        )


if __name__ == "__main__":
    unittest.main()
