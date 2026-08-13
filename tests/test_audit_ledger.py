"""Tests for audit ledger event fetchers."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from api.audit_ledger import (
    _fetch_manual_deposit_events,
    build_ledger_lines,
    cashout_method_token,
    cashout_source_label,
    fetch_cashout_events,
    LedgerEvent,
)


class ManualDepositEventsTestCase(unittest.TestCase):
    @patch("api.audit_ledger.payment_in_audit_day_for_club", return_value=True)
    @patch("api.audit_ledger._apply_audit_manual_filters")
    @patch("api.audit_ledger.audit_day_window_utc")
    def test_skips_resolve_group_title_when_chat_id_missing(
        self,
        _mock_window,
        mock_filters,
        _mock_in_day,
    ):
        payment = MagicMock()
        payment.id = 99
        payment.created_at = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)

        query = MagicMock()
        query.order_by.return_value.all.return_value = [payment]
        mock_filters.return_value = query

        session = MagicMock()

        def build_read(_session, _payment):
            return {
                "created_at": payment.created_at,
                "club_id": 1,
                "gg_player_id": None,
                "telegram_chat_id": None,
                "amount_usd": Decimal("25.00"),
            }

        with patch("api.audit_ledger.resolve_group_title") as mock_resolve:
            events = _fetch_manual_deposit_events(
                session,
                MagicMock(),
                build_read,
                club_slug="round-table",
                audit_date=date(2026, 7, 5),
                from_dt=datetime(2026, 7, 5, 4, 0, tzinfo=timezone.utc),
                to_dt=datetime(2026, 7, 6, 4, 59, 59, tzinfo=timezone.utc),
                source="deposit_zelle",
            )

        mock_resolve.assert_not_called()
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0].gg_player_id)
        self.assertEqual(events[0].amount_usd, Decimal("25.00"))


class CashoutSourceLabelTestCase(unittest.TestCase):
    def test_aliases_and_suboptions(self):
        self.assertEqual(cashout_method_token("venmo"), "Venmo")
        self.assertEqual(cashout_method_token("Cash App"), "Cash App")
        self.assertEqual(cashout_method_token("cashapp"), "Cash App")
        self.assertEqual(cashout_method_token("Venmo — first deposit"), "Venmo")
        self.assertEqual(cashout_method_token("PayPal email"), "PayPal")
        self.assertIsNone(cashout_method_token("Other"))
        self.assertIsNone(cashout_method_token(""))

    def test_join_unique_in_order(self):
        self.assertEqual(
            cashout_source_label(["Venmo", "Zelle", "Venmo"]),
            "Cashout Venmo + Zelle",
        )
        self.assertEqual(cashout_source_label(["cashapp"]), "Cashout Cash App")
        self.assertEqual(cashout_source_label(["Other", None, ""]), "Cashout")
        self.assertEqual(
            cashout_source_label(["Venmo", "Unknown", "Crypto"]),
            "Cashout Venmo + Crypto",
        )


class BuildLedgerLinesSourceLabelTestCase(unittest.TestCase):
    def test_uses_cashout_override(self):
        events = [
            LedgerEvent(
                "cashout",
                "1111-2222",
                Decimal("40"),
                None,
                "cashout:1",
                source_label="Cashout Venmo",
            ),
        ]
        lines = build_ledger_lines(events)
        self.assertEqual(lines[0].source_label, "Cashout Venmo")
        self.assertIsNone(lines[0].variant)

    def test_falls_back_when_override_blank(self):
        events = [
            LedgerEvent("cashout", "1111-2222", Decimal("40"), None, "cashout:1"),
        ]
        lines = build_ledger_lines(events)
        self.assertEqual(lines[0].source_label, "Cashout")


class FetchCashoutEventsTestCase(unittest.TestCase):
    @patch("api.audit_ledger.payment_in_audit_day_for_club", return_value=True)
    @patch("api.audit_ledger.audit_day_window_utc")
    @patch("api.audit_ledger.resolve_club_id", return_value=4)
    def test_labels_from_payments(
        self,
        _mock_club,
        mock_window,
        _mock_in_day,
    ):
        mock_window.return_value = (
            datetime(2026, 7, 5, 4, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 6, 3, 59, 59, tzinfo=timezone.utc),
        )
        venmo = MagicMock()
        venmo.method_display_name = "Venmo"
        venmo.sort_order = 0
        venmo.id = 1
        zelle = MagicMock()
        zelle.method_display_name = "Zelle"
        zelle.sort_order = 1
        zelle.id = 2
        record = MagicMock()
        record.id = 10
        record.club_id = 4
        record.created_at = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
        record.gg_player_id = "1111-2222"
        record.group_title = "GTO / 1111-2222 / P"
        record.amount = Decimal("50")
        record.payments = [zelle, venmo]

        query = MagicMock()
        query.options.return_value.filter.return_value.order_by.return_value.all.return_value = [
            record
        ]
        session = MagicMock()
        session.query.return_value = query

        events = fetch_cashout_events(
            session, club_slug="clubgto", audit_date=date(2026, 7, 5)
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source_label, "Cashout Venmo + Zelle")
        self.assertIsNone(events[0].variant)
        self.assertEqual(events[0].amount_usd, Decimal("50"))


if __name__ == "__main__":
    unittest.main()
