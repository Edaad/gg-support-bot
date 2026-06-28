"""Tests for inactive outreach re-onboard helpers."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bot.services.inactive_group_outreach_reonboard import _mark_outreach_reonboarded
from db.models import InactiveGroupOutreachRow


class TestMarkOutreachReonboarded(unittest.TestCase):
    @patch("bot.services.inactive_group_outreach_reonboard.get_db")
    def test_sets_reonboarded_status(self, mock_get_db: MagicMock) -> None:
        row = InactiveGroupOutreachRow(
            id=7,
            club_key="round_table",
            telegram_chat_id=-10099,
            group_title="RT / 1-2 / Test",
            scan_status="scanned",
            dm_status="sent",
        )
        session = MagicMock()
        session.get.return_value = row
        mock_get_db.return_value.__enter__.return_value = session

        _mark_outreach_reonboarded(7, new_chat_id=-10055)

        self.assertEqual(row.dm_status, "reonboarded")
        self.assertEqual(row.reonboard_new_chat_id, -10055)
        session.commit.assert_called_once()

    @patch("bot.services.inactive_group_outreach_reonboard.get_db")
    def test_sets_failed_status(self, mock_get_db: MagicMock) -> None:
        row = InactiveGroupOutreachRow(
            id=8,
            club_key="round_table",
            telegram_chat_id=-10099,
            group_title="RT / 1-2 / Test",
            scan_status="scanned",
            dm_status="sent",
        )
        session = MagicMock()
        session.get.return_value = row
        mock_get_db.return_value.__enter__.return_value = session

        _mark_outreach_reonboarded(8, error="erase_failed")

        self.assertEqual(row.dm_status, "reonboard_failed")
        self.assertEqual(row.reonboard_error, "erase_failed")
