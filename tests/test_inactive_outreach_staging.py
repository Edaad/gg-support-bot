"""Tests for inactive group outreach manual staging."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from bot.services.inactive_group_outreach_staging import (
    STAGE_STATUS_STAGED,
    STAGE_STATUS_UNSTAGED,
    is_megagroup_chat_id,
    list_staged_groups,
    stage_inactive_group,
    unstage_inactive_group,
)
from db.models import InactiveGroupOutreachRow


class TestMegagroupValidation(unittest.TestCase):
    def test_accepts_supergroup_ids(self) -> None:
        self.assertTrue(is_megagroup_chat_id(-1003931597118))

    def test_rejects_basic_group_ids(self) -> None:
        self.assertFalse(is_megagroup_chat_id(-4923950386))


class TestStageInactiveGroup(unittest.TestCase):
    def _mock_session(self) -> MagicMock:
        session = MagicMock()
        session._store = {}

        def query(model):
            assert model is InactiveGroupOutreachRow
            q = MagicMock()

            def filter_by(**kwargs):
                key = (kwargs["club_key"], int(kwargs["telegram_chat_id"]))
                row = session._store.get(key)

                result = MagicMock()

                def first():
                    return row

                result.first = first
                return result

            q.filter_by = filter_by
            return q

        def add(row):
            key = (row.club_key, int(row.telegram_chat_id))
            if row.id is None:
                row.id = len(session._store) + 1
            session._store[key] = row

        session.query = query
        session.add = add
        session.refresh = lambda row: None
        session.commit = lambda: None
        return session

    @patch("bot.services.inactive_group_outreach_staging.get_db")
    def test_creates_staged_row(self, mock_get_db: MagicMock) -> None:
        session = self._mock_session()
        mock_get_db.return_value.__enter__.return_value = session

        result = stage_inactive_group(
            club_key="round_table",
            telegram_chat_id=-1003931597118,
            group_title="RT / 1234-5678 / Test",
            staged_by_user_id=99,
            note="reviewed",
        )

        self.assertTrue(result.ok)
        self.assertFalse(result.already_staged)
        self.assertFalse(result.has_scan_data)
        row = session._store[("round_table", -1003931597118)]
        self.assertEqual(row.stage_status, STAGE_STATUS_STAGED)
        self.assertEqual(row.staged_by_telegram_user_id, 99)
        self.assertEqual(row.stage_note, "reviewed")
        self.assertEqual(row.gg_player_id, "1234-5678")
        self.assertEqual(row.scan_status, "pending")

    @patch("bot.services.inactive_group_outreach_staging.get_db")
    def test_restage_preserves_scan_fields(self, mock_get_db: MagicMock) -> None:
        session = self._mock_session()
        existing = InactiveGroupOutreachRow(
            id=7,
            club_key="round_table",
            telegram_chat_id=-1003931597118,
            group_title="RT / 1234-5678 / Test",
            inactive_90d=True,
            inactive_180d=True,
            scan_status="scanned",
            scanned_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            stage_status=STAGE_STATUS_UNSTAGED,
        )
        session._store = {("round_table", -1003931597118): existing}
        mock_get_db.return_value.__enter__.return_value = session

        result = stage_inactive_group(
            club_key="round_table",
            telegram_chat_id=-1003931597118,
            group_title="RT / 1234-5678 / Test",
            staged_by_user_id=100,
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.has_scan_data)
        self.assertTrue(result.inactive_180d)
        self.assertEqual(existing.scan_status, "scanned")
        self.assertEqual(existing.stage_status, STAGE_STATUS_STAGED)

    @patch("bot.services.inactive_group_outreach_staging.get_db")
    def test_already_staged_is_idempotent(self, mock_get_db: MagicMock) -> None:
        session = self._mock_session()
        existing = InactiveGroupOutreachRow(
            id=3,
            club_key="round_table",
            telegram_chat_id=-1003931597118,
            group_title="RT / 1234-5678 / Test",
            stage_status=STAGE_STATUS_STAGED,
            staged_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            staged_by_telegram_user_id=1,
        )
        session._store = {("round_table", -1003931597118): existing}
        mock_get_db.return_value.__enter__.return_value = session

        result = stage_inactive_group(
            club_key="round_table",
            telegram_chat_id=-1003931597118,
            group_title="RT / 1234-5678 / Test",
            staged_by_user_id=2,
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.already_staged)

    def test_rejects_basic_group(self) -> None:
        result = stage_inactive_group(
            club_key="round_table",
            telegram_chat_id=-4923950386,
            group_title="RT AT / 1466-1419 / Richard",
            staged_by_user_id=1,
        )
        self.assertFalse(result.ok)
        self.assertIn("megagroup", (result.error or "").lower())


class TestUnstageAndList(unittest.TestCase):
    @patch("bot.services.inactive_group_outreach_staging.get_db")
    def test_unstage_clears_staging_fields(self, mock_get_db: MagicMock) -> None:
        row = InactiveGroupOutreachRow(
            id=5,
            club_key="round_table",
            telegram_chat_id=-100111,
            group_title="RT / 1-2 / A",
            stage_status=STAGE_STATUS_STAGED,
            staged_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            staged_by_telegram_user_id=9,
        )
        session = MagicMock()
        session.get.return_value = row
        mock_get_db.return_value.__enter__.return_value = session

        result = unstage_inactive_group(row_id=5)

        self.assertTrue(result.ok)
        self.assertEqual(row.stage_status, STAGE_STATUS_UNSTAGED)
        self.assertIsNone(row.staged_at)
        self.assertIsNone(row.staged_by_telegram_user_id)
        session.commit.assert_called_once()

    @patch("bot.services.inactive_group_outreach_staging.get_db")
    def test_list_returns_only_staged_rows(self, mock_get_db: MagicMock) -> None:
        staged = InactiveGroupOutreachRow(
            id=1,
            club_key="round_table",
            telegram_chat_id=-100111,
            group_title="A",
            stage_status=STAGE_STATUS_STAGED,
            staged_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
            scanned_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            inactive_90d=True,
            inactive_180d=False,
        )
        session = MagicMock()
        query = MagicMock()
        session.query.return_value = query
        query.filter.return_value = query
        query.order_by.return_value = query
        query.limit.return_value = query
        query.all.return_value = [staged]
        mock_get_db.return_value.__enter__.return_value = session

        rows = list_staged_groups(club_key="round_table", limit=10)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, 1)
        self.assertTrue(rows[0].has_scan_data)
        self.assertTrue(rows[0].inactive_90d)


if __name__ == "__main__":
    unittest.main()
