"""Tests for audit ledger event fetchers."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from api.audit_ledger import _fetch_manual_deposit_events


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


if __name__ == "__main__":
    unittest.main()
