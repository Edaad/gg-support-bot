"""Tests for the /earlyrb entry point.

Early feeback has **no** daily limit: a player may claim as often as they have
feeback remaining, and Elevate's ``nothing_remaining`` is what stops a second
claim. The only spacing is the short re-check throttle that protects the
single-threaded screen robot.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.earlyrb import (
    ADDING_COPY,
    ADDING_IN_PROGRESS_COPY,
    EARLYRB_CONFIRM,
    EARLYRB_ELIGIBLE_MESSAGE,
    earlyrb_cancel,
    earlyrb_claim,
    earlyrb_entry,
)
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

        mock_record.assert_called_once_with(1, 111, update.effective_chat.id, "earlyrb")
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
    @patch("bot.handlers.earlyrb.record_activity", side_effect=RuntimeError("db down"))
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
            "Early feeback counts as a deposit and will reset the cashout timer",
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
        update.message.reply_text.assert_called_once_with("please wait a few minutes")


class ClaimStatusMessageTests(unittest.IsolatedAsyncioTestCase):
    def _make(self):
        status = MagicMock()
        status.edit_text = AsyncMock()
        chat = MagicMock()
        chat.id = -100
        chat.send_message = AsyncMock(return_value=status)
        update = MagicMock()
        update.callback_query = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_reply_markup = AsyncMock()
        update.effective_chat = chat
        update.effective_user = MagicMock()
        update.effective_user.id = 111
        context = MagicMock()
        context.chat_data = {
            "earlyrb_club_id": 1,
            "earlyrb_chat_id": -100,
            "earlyrb_user_id": 111,
            "earlyrb_title": "RT / 8272-5942 / P",
            "earlyrb_fee": MagicMock(),
            "earlyrb_quote": MagicMock(nickname="P"),
            "earlyrb_target": MagicMock(),
            "earlyrb_player_id": "8272-5942",
        }
        return update, context, chat, status

    async def test_adding_status_is_replaced_with_the_result(self) -> None:
        update, context, chat, status = self._make()
        result = SimpleNamespace(
            kind="added",
            player_message="$12.00 feeback added to your account!",
            quote=None,
        )
        with (
            patch(
                "bot.handlers.earlyrb.handle_stale_flow_callback",
                AsyncMock(return_value=False),
            ),
            patch(
                "bot.services.early_rakeback_auto.claim_feeback",
                AsyncMock(return_value=result),
            ),
            patch("bot.services.early_rakeback_auto.create_claim_row", return_value=7),
            patch(
                "bot.services.early_rakeback_auto.new_idempotency_key", return_value="k"
            ),
        ):
            state = await earlyrb_claim(update, context)

        self.assertEqual(state, ConversationHandler.END)
        chat.send_message.assert_awaited_once_with(ADDING_COPY)
        status.edit_text.assert_awaited_once_with(
            "$12.00 feeback added to your account!"
        )


class LookupCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_lookup_does_not_prompt_claim(self) -> None:
        from bot.handlers.earlyrb import _cleanup, _run_lookup
        from bot.services.early_rakeback_auto import FINDING_FEE_COPY, CALCULATING_COPY

        chat = MagicMock()
        chat.send_message = AsyncMock()
        update = MagicMock()
        update.effective_chat = chat
        context = MagicMock()
        context.chat_data = {
            "earlyrb_club_id": 1,
            "earlyrb_chat_id": -100,
            "earlyrb_user_id": 111,
            "earlyrb_title": "t",
        }

        async def fee_then_cancel(**_kwargs):
            _cleanup(context)
            return SimpleNamespace(kind="ok", fee=MagicMock(), detail="")

        with (
            patch("bot.handlers.earlyrb.record_activity_for_chat"),
            patch("bot.services.early_rakeback_auto.check_fee", fee_then_cancel),
        ):
            state = await _run_lookup(update, context)

        self.assertEqual(state, ConversationHandler.END)
        sent = [c.args[0] for c in chat.send_message.await_args_list]
        self.assertIn(FINDING_FEE_COPY, sent)
        self.assertNotIn(CALCULATING_COPY, sent)


class CancelDuringAddTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_during_chip_add_is_refused(self) -> None:
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.chat_data = {
            "earlyrb_club_id": 1,
            "earlyrb_chat_id": -100,
            "earlyrb_committing": True,
        }

        state = await earlyrb_cancel(update, context)

        self.assertEqual(state, EARLYRB_CONFIRM)
        update.message.reply_text.assert_awaited_once_with(ADDING_IN_PROGRESS_COPY)
        self.assertEqual(context.chat_data.get("earlyrb_club_id"), 1)


class HandlerRegistrationTests(unittest.TestCase):
    def test_long_rpa_callbacks_are_non_blocking(self) -> None:
        from bot.handlers.earlyrb import get_earlyrb_handler

        handler = get_earlyrb_handler()
        self.assertFalse(handler.entry_points[0].block)
        union_cb = handler.states[0][0]
        self.assertFalse(union_cb.block)
        confirm_cbs = handler.states[1]
        self.assertFalse(confirm_cbs[0].block)
        self.assertIn(ConversationHandler.WAITING, handler.states)


if __name__ == "__main__":
    unittest.main()
