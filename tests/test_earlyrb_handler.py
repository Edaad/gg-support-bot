"""Tests for the /earlyrb entry point.

Early feeback has **no** daily limit: a player may claim as often as they have
feeback remaining, and Elevate's ``nothing_remaining`` is what stops a second
claim. The only spacing is the short re-check throttle that protects the
single-threaded screen robot.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.earlyrb import EARLYRB_ELIGIBLE_MESSAGE, earlyrb_entry
from telegram.ext import ConversationHandler


class CannedFallbackTestCase(unittest.IsolatedAsyncioTestCase):
    """With the auto toggle off (or either API unconfigured) nothing changed."""

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

    @patch("bot.handlers.earlyrb.auto_earlyrb_available", return_value=False)
    @patch("bot.handlers.earlyrb.record_activity")
    @patch("bot.handlers.earlyrb.update_group_name")
    @patch("bot.handlers.earlyrb.get_club_for_chat", return_value=1)
    async def test_records_and_replies(
        self, _club, _rename, mock_record, _auto
    ) -> None:
        update = self._make_update()

        await earlyrb_entry(update, MagicMock())

        mock_record.assert_called_once_with(
            1, 111, update.effective_chat.id, "earlyrb"
        )
        update.message.reply_text.assert_called_once_with(EARLYRB_ELIGIBLE_MESSAGE)

    @patch("bot.handlers.earlyrb.auto_earlyrb_available", return_value=False)
    @patch("bot.handlers.earlyrb.record_activity")
    @patch("bot.handlers.earlyrb.update_group_name")
    @patch("bot.handlers.earlyrb.get_club_for_chat", return_value=1)
    async def test_back_to_back_requests_are_both_allowed(
        self, _club, _rename, _record, _auto
    ) -> None:
        """No 24h gate: a second /earlyrb seconds later is answered the same way."""
        for _ in range(2):
            update = self._make_update()
            await earlyrb_entry(update, MagicMock())
            update.message.reply_text.assert_called_once_with(EARLYRB_ELIGIBLE_MESSAGE)

    @patch("bot.handlers.earlyrb.auto_earlyrb_available", return_value=False)
    @patch(
        "bot.handlers.earlyrb.record_activity", side_effect=RuntimeError("db down")
    )
    @patch("bot.handlers.earlyrb.update_group_name")
    @patch("bot.handlers.earlyrb.get_club_for_chat", return_value=1)
    async def test_record_failure_does_not_send_success(
        self, _club, _rename, mock_record, _auto
    ) -> None:
        update = self._make_update()

        await earlyrb_entry(update, MagicMock())

        mock_record.assert_called_once()
        update.message.reply_text.assert_called_once()
        self.assertNotIn(
            "We're checking your early rakeback",
            update.message.reply_text.call_args[0][0],
        )

    async def test_non_group_chat_is_refused(self) -> None:
        update = self._make_update()
        update.effective_chat.type = "private"

        state = await earlyrb_entry(update, MagicMock())

        self.assertEqual(state, ConversationHandler.END)
        self.assertIn("club group", update.message.reply_text.call_args[0][0])


class CopyTestCase(unittest.TestCase):
    def test_canned_message_keeps_the_cashout_timer_warning(self) -> None:
        self.assertIn(
            "Early rake back counts as a deposit and will reset the cashout timer",
            EARLYRB_ELIGIBLE_MESSAGE,
        )

    def test_canned_message_claims_no_daily_limit(self) -> None:
        lowered = EARLYRB_ELIGIBLE_MESSAGE.lower()
        self.assertNotIn("24 hour", lowered)
        self.assertNotIn("once every", lowered)


class RecheckThrottleTestCase(unittest.IsolatedAsyncioTestCase):
    """The throttle only spaces out fee lookups; it is not a daily limit."""

    def _make_update(self):
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_chat = MagicMock()
        update.effective_chat.id = -100
        update.effective_chat.type = "supergroup"
        update.effective_chat.title = "Player 12345"
        update.effective_user = MagicMock()
        update.effective_user.id = 111
        return update

    @patch("bot.handlers.earlyrb.check_earlyrb_recheck_throttle")
    @patch(
        "bot.handlers.earlyrb.block_if_group_money_flow_active",
        new_callable=AsyncMock,
    )
    @patch("bot.handlers.earlyrb.auto_earlyrb_available", return_value=True)
    @patch("bot.handlers.earlyrb.update_group_name")
    @patch("bot.handlers.earlyrb.get_club_for_chat", return_value=1)
    async def test_throttled_lookup_ends_the_flow(
        self, _club, _rename, _auto, mock_block, mock_throttle
    ) -> None:
        mock_block.return_value = False
        mock_throttle.return_value = (False, "please wait a few minutes")
        update = self._make_update()

        state = await earlyrb_entry(update, MagicMock())

        self.assertEqual(state, ConversationHandler.END)
        update.message.reply_text.assert_called_once_with(
            "please wait a few minutes"
        )


if __name__ == "__main__":
    unittest.main()
