"""Unit tests for audit net reconcile engine."""

from __future__ import annotations

import os
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from api.audit_ledger import (
    LedgerEvent,
    aggregate_ledger_by_player,
)
from api.audit_reconcile import (
    RECONCILE_MATCH_TOLERANCE_USD,
    aggregate_trade_record,
    run_audit_reconcile,
    _within_match_tolerance,
)
from api.glide_audit_sync import dedupe_glide_events
from api.gg_computer_settlement import is_monday_audit_date
from db.models import (
    Club,
    EarlyRakebackSnapshot,
    TradeRecordLine,
    TradeRecordUpload,
)


class TradeRecordAggregationTestCase(unittest.TestCase):
    def test_sums_signed_amounts_per_player(self):
        upload = TradeRecordUpload(id=1, club_slug="round-table", audit_date=date(2026, 6, 19))
        lines = [
            TradeRecordLine(
                id=1,
                upload_id=1,
                sheet_row=5,
                amount=Decimal("100.00"),
                member_gg_player_id="3011-9668",
            ),
            TradeRecordLine(
                id=2,
                upload_id=1,
                sheet_row=6,
                amount=Decimal("-25.50"),
                member_gg_player_id="3011-9668",
            ),
            TradeRecordLine(
                id=3,
                upload_id=1,
                sheet_row=7,
                amount=Decimal("50.00"),
                member_gg_player_id="3011-9999",
            ),
        ]
        session = MagicMock()
        session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = (
            lines
        )

        by_player, _counts, unmatched = aggregate_trade_record(session, upload=upload)
        self.assertEqual(by_player["3011-9668"], Decimal("74.50"))
        self.assertEqual(by_player["3011-9999"], Decimal("50.00"))
        self.assertEqual(unmatched, [])

    def test_unmatched_trade_rows_without_gg_id(self):
        upload = TradeRecordUpload(id=1, club_slug="round-table", audit_date=date(2026, 6, 19))
        lines = [
            TradeRecordLine(
                id=1,
                upload_id=1,
                sheet_row=5,
                amount=Decimal("10.00"),
                member_gg_player_id=None,
                member_nickname="Ghost",
            ),
            TradeRecordLine(
                id=2,
                upload_id=1,
                sheet_row=6,
                amount=Decimal("0"),
                member_gg_player_id=None,
            ),
        ]
        session = MagicMock()
        session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = (
            lines
        )

        by_player, _counts, unmatched = aggregate_trade_record(session, upload=upload)
        self.assertEqual(by_player, {})
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0].amount, Decimal("10.00"))
        self.assertEqual(unmatched[0].member_nickname, "Ghost")


class MatchToleranceTestCase(unittest.TestCase):
    def test_tolerance_constant(self):
        self.assertEqual(RECONCILE_MATCH_TOLERANCE_USD, Decimal("2"))

    def test_within_two_dollar_band(self):
        self.assertTrue(_within_match_tolerance(Decimal("0")))
        self.assertTrue(_within_match_tolerance(Decimal("2")))
        self.assertTrue(_within_match_tolerance(Decimal("-2")))
        self.assertTrue(_within_match_tolerance(Decimal("1.99")))
        self.assertFalse(_within_match_tolerance(Decimal("2.01")))
        self.assertFalse(_within_match_tolerance(Decimal("-2.01")))


class LedgerAggregationTestCase(unittest.TestCase):
    def test_net_ledger_formula(self):
        events = [
            LedgerEvent("deposit_stripe", "3011-9668", Decimal("100"), None, "d:1"),
            LedgerEvent("early_rakeback", "3011-9668", Decimal("25"), None, "e:1"),
            LedgerEvent("bonus", "3011-9668", Decimal("10"), None, "b:1"),
            LedgerEvent("monday_settlement", "3011-9668", Decimal("5"), None, "m:1"),
            LedgerEvent("glide", "3011-9668", Decimal("3"), None, "g:1"),
            LedgerEvent("cashout", "3011-9668", Decimal("40"), None, "c:1"),
        ]
        by_player, unmatched = aggregate_ledger_by_player(events)
        self.assertEqual(unmatched, [])
        bd = by_player["3011-9668"]
        self.assertEqual(bd.deposits, Decimal("100"))
        self.assertEqual(bd.early_rb, Decimal("25"))
        self.assertEqual(bd.bonuses, Decimal("10"))
        self.assertEqual(bd.monday, Decimal("5"))
        self.assertEqual(bd.glide, Decimal("3"))
        self.assertEqual(bd.cashouts, Decimal("40"))
        self.assertEqual(bd.net, Decimal("103"))

    def test_unmatched_ledger_without_gg_id(self):
        events = [
            LedgerEvent("bonus", None, Decimal("15"), None, "b:1", detail="@unknown"),
        ]
        by_player, unmatched = aggregate_ledger_by_player(events)
        self.assertEqual(by_player, {})
        self.assertEqual(len(unmatched), 1)


class GlideDedupeTestCase(unittest.TestCase):
    def test_drops_matching_postgres_deposit(self):
        existing = [
            LedgerEvent("deposit_stripe", "3011-9668", Decimal("100"), None, "d:1"),
        ]
        glide = [
            LedgerEvent("glide", "3011-9668", Decimal("100"), None, "g:1"),
            LedgerEvent("glide", "3011-9668", Decimal("5"), None, "g:2"),
        ]
        deduped = dedupe_glide_events(glide, existing)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].amount_usd, Decimal("5"))


class ReconcileBlockedTestCase(unittest.TestCase):
    def setUp(self):
        self.club = Club(id=2, name="Round Table", telegram_user_id=1)
        self.mock_db = MagicMock()

        def query_model(model):
            q = MagicMock()
            if model is Club:
                q.filter.return_value.params.return_value.first.return_value = self.club
                q.filter.return_value.first.return_value = self.club
                return q
            if model is TradeRecordUpload:
                q.filter_by.return_value.first.return_value = None
                return q
            return q

        self.mock_db.query.side_effect = query_model

    @patch("api.audit_reconcile.resolve_club_id", return_value=2)
    def test_blocked_without_trade_record(self, _resolve):
        report = run_audit_reconcile(
            self.mock_db,
            club_slug="round-table",
            audit_date=date(2026, 6, 19),
            persist=False,
        )
        self.assertEqual(report.status, "blocked")
        self.assertIn("trade record", (report.blocked_reason or "").lower())


class ReconcilePassFailTestCase(unittest.TestCase):
    def setUp(self):
        self.club = Club(id=2, name="Round Table", telegram_user_id=1)
        self.upload = TradeRecordUpload(
            id=10,
            club_id=2,
            club_slug="round-table",
            audit_date=date(2026, 6, 18),
        )
        self.lines = [
            TradeRecordLine(
                id=1,
                upload_id=10,
                sheet_row=5,
                amount=Decimal("100.00"),
                member_gg_player_id="3011-9668",
            ),
        ]
        self.mock_db = MagicMock()

        def query_model(model):
            q = MagicMock()
            if model is Club:
                q.filter.return_value.params.return_value.first.return_value = self.club
                q.filter.return_value.first.return_value = self.club
                return q
            if model is TradeRecordUpload:
                q.filter_by.return_value.first.return_value = self.upload
                return q
            if model is TradeRecordLine:
                q.filter_by.return_value.order_by.return_value.all.return_value = self.lines
                return q
            if model is EarlyRakebackSnapshot:
                q.filter_by.return_value.first.return_value = None
                return q
            return q

        self.mock_db.query.side_effect = query_model

    @patch("api.audit_reconcile.fetch_glide_ledger_events", return_value=([], []))
    @patch("api.audit_reconcile.fetch_settlement_events", return_value=([], []))
    @patch("api.audit_reconcile.fetch_cashout_events", return_value=[])
    @patch("api.audit_reconcile.fetch_bonus_events", return_value=[])
    @patch("api.audit_reconcile.fetch_early_rakeback_events", return_value=[])
    @patch("api.audit_reconcile.fetch_deposit_events")
    @patch("api.audit_reconcile.resolve_club_id", return_value=2)
    @patch("api.audit_reconcile.is_monday_audit_date", return_value=False)
    def test_pass_when_delta_zero(
        self,
        _monday,
        _resolve,
        mock_deposits,
        *_rest,
    ):
        mock_deposits.return_value = [
            LedgerEvent("deposit_stripe", "3011-9668", Decimal("100"), None, "d:1"),
        ]
        report = run_audit_reconcile(
            self.mock_db,
            club_slug="round-table",
            audit_date=date(2026, 6, 18),
            persist=False,
        )
        self.assertEqual(report.status, "pass")
        self.assertEqual(report.players_matched, 1)
        self.assertEqual(report.players_failed, 0)

    @patch("api.audit_reconcile.fetch_glide_ledger_events", return_value=([], []))
    @patch("api.audit_reconcile.fetch_settlement_events", return_value=([], []))
    @patch("api.audit_reconcile.fetch_cashout_events", return_value=[])
    @patch("api.audit_reconcile.fetch_bonus_events", return_value=[])
    @patch("api.audit_reconcile.fetch_early_rakeback_events", return_value=[])
    @patch("api.audit_reconcile.fetch_deposit_events")
    @patch("api.audit_reconcile.resolve_club_id", return_value=2)
    @patch("api.audit_reconcile.is_monday_audit_date", return_value=False)
    def test_pass_within_two_dollar_tolerance(
        self,
        _monday,
        _resolve,
        mock_deposits,
        *_rest,
    ):
        mock_deposits.return_value = [
            LedgerEvent("deposit_stripe", "3011-9668", Decimal("98.50"), None, "d:1"),
        ]
        report = run_audit_reconcile(
            self.mock_db,
            club_slug="round-table",
            audit_date=date(2026, 6, 18),
            persist=False,
        )
        self.assertEqual(report.status, "pass")
        self.assertEqual(report.players[0].status, "match")
        self.assertEqual(report.players[0].delta, Decimal("1.50"))

    @patch("api.audit_reconcile.fetch_glide_ledger_events", return_value=([], []))
    @patch("api.audit_reconcile.fetch_settlement_events", return_value=([], []))
    @patch("api.audit_reconcile.fetch_cashout_events", return_value=[])
    @patch("api.audit_reconcile.fetch_bonus_events", return_value=[])
    @patch("api.audit_reconcile.fetch_early_rakeback_events", return_value=[])
    @patch("api.audit_reconcile.fetch_deposit_events")
    @patch("api.audit_reconcile.resolve_club_id", return_value=2)
    @patch("api.audit_reconcile.is_monday_audit_date", return_value=False)
    def test_fail_on_mismatch(
        self,
        _monday,
        _resolve,
        mock_deposits,
        *_rest,
    ):
        mock_deposits.return_value = [
            LedgerEvent("deposit_stripe", "3011-9668", Decimal("90"), None, "d:1"),
        ]
        report = run_audit_reconcile(
            self.mock_db,
            club_slug="round-table",
            audit_date=date(2026, 6, 18),
            persist=False,
        )
        self.assertEqual(report.status, "fail")
        self.assertEqual(report.players_failed, 1)
        self.assertEqual(report.players[0].delta, Decimal("10"))


class MondaySettlementTestCase(unittest.TestCase):
    def test_monday_detection(self):
        self.assertTrue(is_monday_audit_date("round-table", date(2026, 6, 22)))
        self.assertFalse(is_monday_audit_date("round-table", date(2026, 6, 18)))

    @patch.dict(os.environ, {"GG_COMPUTER_BASE_URL": "http://gg-computer.test"})
    @patch("api.gg_computer_settlement.httpx.Client")
    def test_settlement_fetch_blocked_on_http_error(self, mock_client_cls):
        import httpx

        from api.gg_computer_settlement import SettlementFetchError, fetch_settlement_events

        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.get.side_effect = httpx.ConnectError("connection refused")

        with self.assertRaises(SettlementFetchError):
            fetch_settlement_events(club_slug="round-table", audit_date=date(2026, 6, 22))


if __name__ == "__main__":
    unittest.main()
