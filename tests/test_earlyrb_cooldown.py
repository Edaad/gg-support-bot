"""Tests for /earlyrb cooldown eligibility and handler."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.earlyrb import EARLYRB_ELIGIBLE_MESSAGE, earlyrb_handler
from bot.services.club import check_earlyrb_eligibility


class EarlyrbEligibilityTestCase(unittest.TestCase):
    @patch("bot.services.club.get_last_activity_by_type")
    @patch("bot.services.club.get_cooldown_settings")
    def test_eligible_when_no_prior_earlyrb(self, mock_settings, mock_last):
        mock_settings.return_value = {"cooldown_hours": 24}
        mock_last.return_value = None

        eligible, msg = check_earlyrb_eligibility(club_id=1, chat_id=100)

        self.assertTrue(eligible)
        self.assertIsNone(msg)
        mock_last.assert_called_once_with(1, 100, "earlyrb")

    @patch("bot.services.club.get_last_activity_by_type")
    @patch("bot.services.club.get_cooldown_settings")
    def test_denied_within_cooldown(self, mock_settings, mock_last):
        mock_settings.return_value = {"cooldown_hours": 24}
        mock_last.return_value = datetime.now(timezone.utc) - timedelta(hours=2)

        eligible, msg = check_earlyrb_eligibility(club_id=1, chat_id=100)

        self.assertFalse(eligible)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("early rakeback requests", msg)
        self.assertIn("50 minimum", msg)

    @patch("bot.services.club.get_last_activity_by_type")
    @patch("bot.services.club.get_cooldown_settings")
    def test_eligible_after_cooldown_expires(self, mock_settings, mock_last):
        mock_settings.return_value = {"cooldown_hours": 24}
        mock_last.return_value = datetime.now(timezone.utc) - timedelta(hours=25)

        eligible, msg = check_earlyrb_eligibility(club_id=1, chat_id=100)

        self.assertTrue(eligible)
        self.assertIsNone(msg)

    @patch("bot.services.club.get_last_activity_by_type")
    @patch("bot.services.club.get_cooldown_settings")
    def test_default_cooldown_hours_when_club_missing(self, mock_settings, mock_last):
        mock_settings.return_value = None
        mock_last.return_value = datetime.now(timezone.utc) - timedelta(hours=2)

        eligible, msg = check_earlyrb_eligibility(club_id=1, chat_id=100)

        self.assertFalse(eligible)
        assert msg is not None
        self.assertIn("24 hours", msg)


class EarlyrbHandlerTestCase(unittest.IsolatedAsyncioTestCase):
    def _make_update(self, *, user_id: int = 111, chat_id: int = -100):
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_chat = MagicMock()
        update.effective_chat.id = chat_id
        update.effective_chat.type = "supergroup"
        update.effective_chat.title = "Player 12345"
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        return update

    @patch("bot.handlers.earlyrb.record_activity")
    @patch("bot.handlers.earlyrb.check_earlyrb_eligibility")
    @patch("bot.handlers.earlyrb.is_club_staff", return_value=False)
    @patch("bot.handlers.earlyrb.update_group_name")
    @patch("bot.handlers.earlyrb.get_club_for_chat")
    async def test_player_eligible_records_and_replies(
        self, mock_club, mock_rename, mock_staff, mock_eligibility, mock_record
    ):
        mock_club.return_value = 1
        mock_eligibility.return_value = (True, None)

        update = self._make_update()
        context = MagicMock()

        await earlyrb_handler(update, context)

        mock_eligibility.assert_called_once_with(1, update.effective_chat.id)
        mock_record.assert_called_once_with(
            1, 111, update.effective_chat.id, "earlyrb"
        )
        update.message.reply_text.assert_called_once_with(EARLYRB_ELIGIBLE_MESSAGE)

    @patch("bot.handlers.earlyrb.record_activity")
    @patch("bot.handlers.earlyrb.check_earlyrb_eligibility")
    @patch("bot.handlers.earlyrb.is_club_staff", return_value=False)
    @patch("bot.handlers.earlyrb.update_group_name")
    @patch("bot.handlers.earlyrb.get_club_for_chat")
    async def test_player_in_cooldown_denied_without_record(
        self, mock_club, mock_rename, mock_staff, mock_eligibility, mock_record
    ):
        mock_club.return_value = 1
        mock_eligibility.return_value = (False, "Please wait.")

        update = self._make_update()
        context = MagicMock()

        await earlyrb_handler(update, context)

        mock_record.assert_not_called()
        update.message.reply_text.assert_called_once_with("Please wait.")

    @patch("bot.handlers.earlyrb.record_activity")
    @patch("bot.handlers.earlyrb.check_earlyrb_eligibility")
    @patch("bot.handlers.earlyrb.is_club_staff")
    @patch("bot.handlers.earlyrb.update_group_name")
    @patch("bot.handlers.earlyrb.get_club_for_chat")
    async def test_staff_skips_eligibility_but_still_records(
        self, mock_club, mock_rename, mock_staff, mock_eligibility, mock_record
    ):
        mock_club.return_value = 1
        mock_staff.return_value = True

        update = self._make_update(user_id=999)
        context = MagicMock()

        await earlyrb_handler(update, context)

        mock_eligibility.assert_not_called()
        mock_record.assert_called_once_with(
            1, 999, update.effective_chat.id, "earlyrb"
        )
        update.message.reply_text.assert_called_once_with(EARLYRB_ELIGIBLE_MESSAGE)


if __name__ == "__main__":
    unittest.main()
