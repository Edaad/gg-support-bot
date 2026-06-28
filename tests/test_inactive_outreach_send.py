"""Tests for /sendinactive outreach DM compose flow."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.constants import ChatType

from bot.handlers.inactive_outreach_send import (
    IO_STEP_KEY,
    _parse_start_args,
    sendinactive_compose_active,
    sendinactive_flow_active,
    sendinactive_message_handler,
)
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


class TestSendinactivePriorityHandler(unittest.IsolatedAsyncioTestCase):
  async def test_compose_handler_runs_when_step_compose(self) -> None:
      update = MagicMock()
      update.message = MagicMock()
      update.message.text = "Hello inactive players"
      update.effective_chat = MagicMock()
      update.effective_chat.type = ChatType.PRIVATE
      update.effective_user = MagicMock()
      update.effective_user.id = 493310710

      context = MagicMock()
      context.user_data = {IO_STEP_KEY: "compose", "io_recipient_count": 1}

      with patch(
          "bot.handlers.inactive_outreach_send._can_use_sendinactive",
          return_value=True,
      ), patch(
          "bot.handlers.inactive_outreach_send.sendinactive_compose",
          new_callable=AsyncMock,
      ) as mock_compose:
          from telegram.ext import ApplicationHandlerStop

          with self.assertRaises(ApplicationHandlerStop):
              await sendinactive_message_handler(update, context)
          mock_compose.assert_awaited_once_with(update, context)

  def test_flow_active_during_compose(self) -> None:
      context = MagicMock()
      context.user_data = {IO_STEP_KEY: "compose"}
      self.assertTrue(sendinactive_flow_active(context))

  def test_compose_active_with_club_key_only(self) -> None:
      context = MagicMock()
      context.user_data = {"io_club_key": "round_table"}
      self.assertTrue(sendinactive_compose_active(context))


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
