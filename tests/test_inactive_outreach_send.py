"""Tests for /sendinactive outreach DM compose flow."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bot.handlers.inactive_outreach_send import _parse_start_args
from bot.services.inactive_group_outreach_dm import arm_dm_campaign, is_dm_batch_running
from db.models import InactiveGroupOutreachControl, InactiveGroupOutreachRow


class TestParseSendinactiveArgs(unittest.TestCase):
    def test_defaults_round_table(self) -> None:
        club, row_id, limit, err = _parse_start_args("/sendinactive")
        self.assertEqual(club, "round_table")
        self.assertIsNone(row_id)
        self.assertIsNone(limit)
        self.assertIsNone(err)

    def test_row_id(self) -> None:
        club, row_id, limit, err = _parse_start_args("/sendinactive row 99")
        self.assertEqual(club, "round_table")
        self.assertEqual(row_id, 99)
        self.assertIsNone(err)

    def test_limit_with_club(self) -> None:
        club, row_id, limit, err = _parse_start_args("/sendinactive round_table limit 1")
        self.assertEqual(club, "round_table")
        self.assertEqual(limit, 1)
        self.assertIsNone(err)


class _FakeDmSession:
    def __init__(self) -> None:
        self.ctrl = InactiveGroupOutreachControl(id=1, dm_batch_status="idle")
        self.rows: list[InactiveGroupOutreachRow] = []

    def get(self, model, pk):
        if model is InactiveGroupOutreachControl and pk == 1:
            return self.ctrl
        return None

    def query(self, model):
        assert model is InactiveGroupOutreachRow
        return self

    def filter(self, *args, **kwargs):
        return self

    def filter_by(self, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self.rows)

    def count(self):
        return len(self.rows)

    def add(self, obj):
        if isinstance(obj, InactiveGroupOutreachControl):
            self.ctrl = obj

    def commit(self):
        return None


class TestArmDmCampaign(unittest.TestCase):
    @patch("db.connection.get_db")
    def test_rejects_empty_message(self, mock_get_db: MagicMock) -> None:
        session = _FakeDmSession()
        mock_get_db.return_value.__enter__.return_value = session
        ok, err, count = arm_dm_campaign(
            club_key="round_table",
            message="  ",
            started_by_user_id=1,
        )
        self.assertFalse(ok)
        self.assertIn("empty", err.lower())

    @patch("bot.services.inactive_group_outreach_dm._eligible_query")
    @patch("db.connection.get_db")
    def test_arms_pending_rows(self, mock_get_db: MagicMock, mock_eligible) -> None:
        session = _FakeDmSession()
        row = InactiveGroupOutreachRow(
            id=1,
            club_key="round_table",
            telegram_chat_id=-1001,
            group_title="RT / 1-2 / x",
            scan_status="scanned",
            entity_resolvable=True,
            player_telegram_user_id=123,
            stage_status="staged",
        )
        mock_get_db.return_value.__enter__.return_value = session

        class _Q:
            def order_by(self, *a, **k):
                return self

            def all(self):
                return [row]

        mock_eligible.return_value = _Q()

        ok, err, count = arm_dm_campaign(
            club_key="round_table",
            message="Please reply",
            started_by_user_id=99,
        )
        self.assertTrue(ok)
        self.assertEqual(count, 1)
        self.assertEqual(session.ctrl.dm_campaign_message, "Please reply")
        self.assertEqual(session.ctrl.dm_batch_status, "running")
        self.assertEqual(row.dm_status, "pending")


class TestDmBatchRunning(unittest.TestCase):
    @patch("db.connection.get_db")
    def test_running_when_status_running(self, mock_get_db: MagicMock) -> None:
        session = _FakeDmSession()
        session.ctrl.dm_batch_status = "running"
        mock_get_db.return_value.__enter__.return_value = session
        self.assertTrue(is_dm_batch_running())
