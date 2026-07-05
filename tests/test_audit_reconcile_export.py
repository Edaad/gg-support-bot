"""Tests for reconcile XLSX export."""

from __future__ import annotations

import io
import unittest
from datetime import date
from decimal import Decimal

from openpyxl import load_workbook

from api.audit_ledger import LedgerBreakdown
from api.audit_reconcile import AuditReconcilePlayerResult, AuditReconcileReport
from api.audit_reconcile_export import (
    DETAIL_HEADERS,
    OVERVIEW_HEADERS,
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
            glide=Decimal("0"),
            cashouts=Decimal("0"),
        ),
        status=status,
    )


class ReconcileExportTestCase(unittest.TestCase):
    def test_workbook_has_overview_and_details_tabs(self):
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
        )

        wb = load_workbook(io.BytesIO(build_reconcile_workbook_from_report(report)))
        self.assertEqual(wb.sheetnames, ["Overview", "Details"])

        overview = wb["Overview"]
        self.assertEqual(overview["A1"].value, "Matched")
        self.assertEqual(
            [overview.cell(row=2, column=c).value for c in range(1, 5)],
            OVERVIEW_HEADERS,
        )
        self.assertEqual(overview["A3"].value, "AcePlayer")
        self.assertEqual(overview["B3"].value, "3011-9668")
        self.assertEqual(overview["A5"].value, "Mismatched")
        self.assertEqual(
            [overview.cell(row=6, column=c).value for c in range(1, 5)],
            OVERVIEW_HEADERS,
        )
        self.assertEqual(overview["A7"].value, "BadPlayer")

        details = wb["Details"]
        self.assertEqual(details["A1"].value, "Matched")
        self.assertEqual(
            [details.cell(row=2, column=c).value for c in range(1, 12)],
            DETAIL_HEADERS,
        )
        self.assertEqual(details["A5"].value, "Mismatched")
        self.assertEqual(details["K7"].value, 10.0)


if __name__ == "__main__":
    unittest.main()
