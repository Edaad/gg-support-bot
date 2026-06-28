"""Tests for inactive outreach DM batch service."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bot.services.inactive_group_outreach_dm import claim_dm_batch, count_dm_eligible_recipients
from db.models import InactiveGroupOutreachRow


class TestClaimDmBatch(unittest.TestCase):
    @patch("db.connection.get_db")
    def test_claim_returns_pending_rows(self, mock_get_db: MagicMock) -> None:
        row = InactiveGroupOutreachRow(
            id=3,
            club_key="round_table",
            telegram_chat_id=-1003931597118,
            group_title="RT / 1-2 / A",
            scan_status="scanned",
            stage_status="staged",
            entity_resolvable=True,
            player_telegram_user_id=555,
            dm_status="pending",
        )

        class _Q:
            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

            def limit(self, n):
                return self

            def all(self):
                return [row]

        session = MagicMock()
        session.query.return_value = _Q()
        mock_get_db.return_value.__enter__.return_value = session

        claimed = claim_dm_batch("round_table", 5)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].player_telegram_user_id, 555)


class TestCountEligible(unittest.TestCase):
    @patch("bot.services.inactive_group_outreach_dm._eligible_query")
    @patch("db.connection.get_db")
    def test_count_with_limit(self, mock_get_db: MagicMock, mock_eligible) -> None:
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session

        class _Q:
            def count(self):
                return 10

        mock_eligible.return_value = _Q()
        self.assertEqual(
            count_dm_eligible_recipients(club_key="round_table", limit=3),
            3,
        )
