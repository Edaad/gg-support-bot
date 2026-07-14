"""Cashout cooldown must ignore earlyrb / command-only activity rows."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from bot.services.club import get_last_activity
from db.models import PlayerActivity


class GetLastActivityTestCase(unittest.TestCase):
    @patch("bot.services.club.get_db")
    def test_filters_to_deposit_and_cashout_only(self, mock_get_db):
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        mock_get_db.return_value.__exit__.return_value = False

        query = session.query.return_value
        filtered = query.filter.return_value
        ordered = filtered.order_by.return_value
        ordered.first.return_value = None

        self.assertIsNone(get_last_activity(1, -100))

        session.query.assert_called_once_with(PlayerActivity)
        filter_args = query.filter.call_args[0]
        type_clause = next(
            arg
            for arg in filter_args
            if getattr(getattr(arg, "left", None), "key", None) == "activity_type"
        )
        allowed = set(type_clause.right.value)
        self.assertEqual(allowed, {"deposit", "cashout"})

    @patch("bot.services.club.get_db")
    def test_returns_normalized_deposit_or_cashout_timestamp(self, mock_get_db):
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        mock_get_db.return_value.__exit__.return_value = False

        ts = datetime(2026, 7, 13, 12, 0, 0)  # naive → treated as UTC
        activity = MagicMock()
        activity.created_at = ts
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            activity
        )

        got = get_last_activity(1, -100)

        self.assertEqual(got, ts.replace(tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
