"""Unit tests for audit net reconcile engine."""

from __future__ import annotations

import os
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from api.audit_ledger import (
    LedgerBreakdown,
    LedgerEvent,
    aggregate_ledger_by_player,
)
from api.audit_reconcile import (
    RECONCILE_MATCH_TOLERANCE_USD,
    AuditReconcilePlayerResult,
    AuditReconcileReport,
    aggregate_trade_record,
    aggregate_trade_records,
    load_stored_reconcile_report,
    report_from_json,
    run_audit_reconcile,
    _report_to_json,
    _within_match_tolerance,
)
from api.glide_audit_sync import dedupe_glide_events
from api.gg_computer_settlement import is_monday_audit_date
from db.models import (
    AuditReconcileRun,
    Club,
    EarlyRakebackLine,
    EarlyRakebackSnapshot,
    PlayerDetails,
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

        by_player, _counts, unmatched, nicknames = aggregate_trade_record(session, upload=upload)
        self.assertEqual(by_player["3011-9668"], Decimal("74.50"))
        self.assertEqual(by_player["3011-9999"], Decimal("50.00"))
        self.assertEqual(unmatched, [])
        self.assertEqual(nicknames, {})

    def test_collects_nickname_from_trade_lines(self):
        upload = TradeRecordUpload(id=1, club_slug="round-table", audit_date=date(2026, 6, 19))
        lines = [
            TradeRecordLine(
                id=1,
                upload_id=1,
                sheet_row=5,
                amount=Decimal("100.00"),
                member_gg_player_id="3011-9668",
                member_nickname="AcePlayer",
            ),
        ]
        session = MagicMock()
        session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = (
            lines
        )

        by_player, _counts, unmatched, nicknames = aggregate_trade_record(session, upload=upload)
        self.assertEqual(by_player["3011-9668"], Decimal("100.00"))
        self.assertEqual(nicknames["3011-9668"], "AcePlayer")
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

        by_player, _counts, unmatched, _nicknames = aggregate_trade_record(session, upload=upload)
        self.assertEqual(by_player, {})
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0].amount, Decimal("10.00"))
        self.assertEqual(unmatched[0].member_nickname, "Ghost")

    def test_combined_uploads_sum_per_player(self):
        rt_upload = TradeRecordUpload(id=1, club_slug="round-table", audit_date=date(2026, 6, 19))
        at_upload = TradeRecordUpload(id=2, club_slug="aces-table", audit_date=date(2026, 6, 19))
        rt_lines = [
            TradeRecordLine(
                id=1,
                upload_id=1,
                sheet_row=5,
                amount=Decimal("100.00"),
                member_gg_player_id="3011-9668",
            ),
        ]
        at_lines = [
            TradeRecordLine(
                id=2,
                upload_id=2,
                sheet_row=5,
                amount=Decimal("25.00"),
                member_gg_player_id="3011-9668",
            ),
            TradeRecordLine(
                id=3,
                upload_id=2,
                sheet_row=6,
                amount=Decimal("10.00"),
                member_gg_player_id="3011-9999",
            ),
        ]
        session = MagicMock()

        def query_model(model):
            q = MagicMock()
            if model is TradeRecordLine:
                def filter_by(**kwargs):
                    inner = MagicMock()
                    uid = kwargs.get("upload_id")
                    if uid == 1:
                        inner.order_by.return_value.all.return_value = rt_lines
                    elif uid == 2:
                        inner.order_by.return_value.all.return_value = at_lines
                    else:
                        inner.order_by.return_value.all.return_value = []
                    return inner

                q.filter_by.side_effect = filter_by
            return q

        session.query.side_effect = query_model

        by_player, _counts, unmatched, _nicknames = aggregate_trade_records(
            session, uploads=[rt_upload, at_upload]
        )
        self.assertEqual(by_player["3011-9668"], Decimal("125.00"))
        self.assertEqual(by_player["3011-9999"], Decimal("10.00"))
        self.assertEqual(unmatched, [])


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
        self.rt_upload = TradeRecordUpload(
            id=10,
            club_id=2,
            club_slug="round-table",
            audit_date=date(2026, 6, 18),
        )
        self.at_upload = TradeRecordUpload(
            id=11,
            club_id=2,
            club_slug="aces-table",
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
                def filter_by(**kwargs):
                    inner = MagicMock()
                    slug = kwargs.get("club_slug")
                    if slug == "round-table":
                        inner.first.return_value = self.rt_upload
                    elif slug == "aces-table":
                        inner.first.return_value = self.at_upload
                    else:
                        inner.first.return_value = None
                    return inner

                q.filter_by.side_effect = filter_by
                return q
            if model is TradeRecordLine:
                def filter_by(**kwargs):
                    inner = MagicMock()
                    uid = kwargs.get("upload_id")
                    if uid == 10:
                        inner.order_by.return_value.all.return_value = self.lines
                    else:
                        inner.order_by.return_value.all.return_value = []
                    return inner

                q.filter_by.side_effect = filter_by
                return q
            if model is EarlyRakebackSnapshot:
                q.filter_by.return_value.first.return_value = None
                return q
            if model is EarlyRakebackLine:
                q.filter_by.return_value.all.return_value = []
                return q
            if model is PlayerDetails:
                q.filter.return_value.all.return_value = []
                return q
            return q

        self.mock_db.query.side_effect = query_model

    @staticmethod
    def _deposits_for_rt_only(_session, club_slug, audit_date, amount):
        if club_slug == "round-table":
            return [
                LedgerEvent("deposit_stripe", "3011-9668", amount, None, "d:1"),
            ]
        return []

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
        mock_deposits.side_effect = lambda s, **kw: self._deposits_for_rt_only(
            s, kw["club_slug"], kw["audit_date"], Decimal("100")
        )
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
        mock_deposits.side_effect = lambda s, **kw: self._deposits_for_rt_only(
            s, kw["club_slug"], kw["audit_date"], Decimal("98.50")
        )
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
        mock_deposits.side_effect = lambda s, **kw: self._deposits_for_rt_only(
            s, kw["club_slug"], kw["audit_date"], Decimal("90")
        )
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


class ReportJsonRoundTripTestCase(unittest.TestCase):
    def test_report_json_round_trip(self):
        report = AuditReconcileReport(
            audit_date=date(2026, 6, 18),
            club_slug="round-table",
            club_name="Round Table",
            status="fail",
            trade_upload_id=10,
            early_rb_snapshot_id=5,
            players=[
                AuditReconcilePlayerResult(
                    gg_player_id="3011-9668",
                    member_nickname="AcePlayer",
                    net_trade_record=Decimal("100"),
                    net_ledger=Decimal("90"),
                    delta=Decimal("10"),
                    ledger_breakdown=LedgerBreakdown(
                        deposits=Decimal("90"),
                        early_rb=Decimal("0"),
                        bonuses=Decimal("0"),
                        monday=Decimal("0"),
                        glide=Decimal("0"),
                        cashouts=Decimal("0"),
                    ),
                    status="mismatch",
                )
            ],
            warnings=["Non-Monday audit day; monday_settlement = 0"],
            players_matched=0,
            players_failed=1,
        )
        raw = _report_to_json(report)
        restored = report_from_json(raw, run_id=42)
        self.assertEqual(restored.audit_date, report.audit_date)
        self.assertEqual(restored.club_slug, report.club_slug)
        self.assertEqual(restored.status, report.status)
        self.assertEqual(restored.run_id, 42)
        self.assertEqual(len(restored.players), 1)
        self.assertEqual(restored.players[0].gg_player_id, "3011-9668")
        self.assertEqual(restored.players[0].member_nickname, "AcePlayer")
        self.assertEqual(restored.players[0].delta, Decimal("10"))
        self.assertEqual(restored.players[0].ledger_breakdown.deposits, Decimal("90"))
        self.assertEqual(restored.warnings, report.warnings)
        self.assertEqual(restored.players_failed, 1)

    def test_load_stored_reconcile_report_returns_none_when_missing(self):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = None
        result = load_stored_reconcile_report(
            session,
            club_slug="round-table",
            audit_date=date(2026, 6, 18),
        )
        self.assertIsNone(result)

    def test_load_stored_reconcile_report_deserializes_run(self):
        report = AuditReconcileReport(
            audit_date=date(2026, 6, 18),
            club_slug="round-table",
            club_name="Round Table",
            status="pass",
            players_matched=1,
            players_failed=0,
        )
        run = AuditReconcileRun(
            id=7,
            club_slug="round-table",
            audit_date=date(2026, 6, 18),
            status="pass",
            players_matched=1,
            players_failed=0,
            unmatched_trade_count=0,
            unmatched_ledger_count=0,
            report_json=_report_to_json(report),
        )
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = run
        loaded = load_stored_reconcile_report(
            session,
            club_slug="round-table",
            audit_date=date(2026, 6, 18),
        )
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.run_id, 7)
        self.assertEqual(loaded.status, "pass")
        self.assertEqual(loaded.players_matched, 1)


if __name__ == "__main__":
    unittest.main()
