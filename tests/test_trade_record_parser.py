"""Tests for trade record XLSX parser."""

from __future__ import annotations

import unittest
from datetime import date

from api.trade_record_parser import (
    TradeRecordParseError,
    TradeRecordValidationError,
    parse_trade_record_workbook,
    validate_metadata,
)
from tests.fixtures.trade_record_xlsx import build_sample_trade_record_xlsx


class TradeRecordParserTestCase(unittest.TestCase):
    def test_parse_extracts_identities_and_transactions(self):
        raw = build_sample_trade_record_xlsx(audit_date=date(2026, 6, 21))
        parsed = parse_trade_record_workbook(raw, audit_date=date(2026, 6, 21))

        self.assertEqual(len(parsed.transactions), 2)
        self.assertEqual(parsed.transactions[0].member_gg_player_id, "3011-9668")
        self.assertEqual(parsed.transactions[0].amount, 100)
        self.assertEqual(parsed.transactions[1].amount, -50)

        ids = {(i.role, i.gg_player_id) for i in parsed.identities}
        self.assertIn(("member", "3011-9668"), ids)
        self.assertIn(("agent", "2000-2001"), ids)
        self.assertIn(("superAgent", "1000-1001"), ids)

    def test_validate_metadata_accepts_matching_club_and_date(self):
        raw = build_sample_trade_record_xlsx(
            club_label="Aces Table",
            audit_date=date(2026, 6, 21),
        )
        parsed = parse_trade_record_workbook(raw, audit_date=date(2026, 6, 21))
        validate_metadata(
            parsed.metadata,
            club_slug="aces-table",
            audit_date=date(2026, 6, 21),
        )

    def test_validate_metadata_rejects_club_mismatch(self):
        raw = build_sample_trade_record_xlsx(club_label="ClubGTO")
        parsed = parse_trade_record_workbook(raw, audit_date=date(2026, 6, 21))
        with self.assertRaises(TradeRecordValidationError):
            validate_metadata(
                parsed.metadata,
                club_slug="aces-table",
                audit_date=date(2026, 6, 21),
            )

    def test_validate_metadata_rejects_date_mismatch(self):
        raw = build_sample_trade_record_xlsx(audit_date=date(2026, 6, 21))
        parsed = parse_trade_record_workbook(raw, audit_date=date(2026, 6, 21))
        with self.assertRaises(TradeRecordValidationError):
            validate_metadata(
                parsed.metadata,
                club_slug="aces-table",
                audit_date=date(2026, 6, 22),
            )

    def test_missing_sheet_raises(self):
        from openpyxl import Workbook
        import io

        wb = Workbook()
        buf = io.BytesIO()
        wb.save(buf)
        with self.assertRaises(TradeRecordParseError):
            parse_trade_record_workbook(buf.getvalue(), audit_date=date(2026, 6, 21))


if __name__ == "__main__":
    unittest.main()
