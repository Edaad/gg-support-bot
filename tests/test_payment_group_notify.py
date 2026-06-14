"""Tests for player-facing payment confirmation in support group chats."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import payment_group_notify as pgn

CHAT_ID = -1001234567890


class FormatPaymentReceivedMessageTestCase(unittest.TestCase):
    def test_whole_dollar_amount(self):
        text = pgn.format_payment_received_message(5000)
        self.assertEqual(
            text,
            "We have received your payment for $50, "
            "chips will be loaded to your account shortly!!",
        )

    def test_large_amount_with_commas(self):
        text = pgn.format_payment_received_message(123400)
        self.assertIn("$1,234", text)


class NotifyPlayerGroupPaymentReceivedTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_sends_message_with_support_bot(self):
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        with (
            patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "prod-token"}, clear=False),
            patch.object(pgn, "Bot", return_value=mock_bot) as bot_cls,
        ):
            ok = await pgn.notify_player_group_payment_received(
                telegram_chat_id=CHAT_ID,
                amount_cents=5000,
            )
        self.assertTrue(ok)
        bot_cls.assert_called_once_with(token="prod-token")
        mock_bot.send_message.assert_awaited_once_with(
            chat_id=CHAT_ID,
            text=(
                "We have received your payment for $50, "
                "chips will be loaded to your account shortly!!"
            ),
        )

    async def test_test_payment_prefers_test_bot_token(self):
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        with (
            patch.dict(
                os.environ,
                {
                    "TELEGRAM_BOT_TOKEN": "prod-token",
                    "TELEGRAM_TEST_BOT_TOKEN": "test-token",
                },
                clear=False,
            ),
            patch.object(pgn, "Bot", return_value=mock_bot) as bot_cls,
        ):
            ok = await pgn.notify_player_group_payment_received(
                telegram_chat_id=CHAT_ID,
                amount_cents=5000,
                is_test=True,
            )
        self.assertTrue(ok)
        bot_cls.assert_called_once_with(token="test-token")

    async def test_test_payment_falls_back_to_prod_token(self):
        failing_bot = MagicMock()
        failing_bot.send_message = AsyncMock(side_effect=RuntimeError("blocked"))
        succeeding_bot = MagicMock()
        succeeding_bot.send_message = AsyncMock()

        with (
            patch.dict(
                os.environ,
                {
                    "TELEGRAM_BOT_TOKEN": "prod-token",
                    "TELEGRAM_TEST_BOT_TOKEN": "test-token",
                },
                clear=False,
            ),
            patch.object(
                pgn,
                "Bot",
                side_effect=[failing_bot, succeeding_bot],
            ) as bot_cls,
        ):
            ok = await pgn.notify_player_group_payment_received(
                telegram_chat_id=CHAT_ID,
                amount_cents=5000,
                is_test=True,
            )
        self.assertTrue(ok)
        self.assertEqual(bot_cls.call_count, 2)
        bot_cls.assert_any_call(token="test-token")
        bot_cls.assert_any_call(token="prod-token")
        succeeding_bot.send_message.assert_awaited_once()

    async def test_missing_token_returns_false(self):
        with patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_TEST_BOT_TOKEN": ""},
            clear=False,
        ):
            ok = await pgn.notify_player_group_payment_received(
                telegram_chat_id=CHAT_ID,
                amount_cents=5000,
            )
        self.assertFalse(ok)

    async def test_telegram_error_returns_false_when_all_tokens_fail(self):
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock(side_effect=RuntimeError("blocked"))
        with (
            patch.dict(
                os.environ,
                {
                    "TELEGRAM_BOT_TOKEN": "prod-token",
                    "TELEGRAM_TEST_BOT_TOKEN": "test-token",
                },
                clear=False,
            ),
            patch.object(pgn, "Bot", return_value=mock_bot),
        ):
            ok = await pgn.notify_player_group_payment_received(
                telegram_chat_id=CHAT_ID,
                amount_cents=5000,
            )
        self.assertFalse(ok)
        self.assertEqual(mock_bot.send_message.await_count, 2)


class MaybeNotifyPlayerOnAutoBoundTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_skips_when_not_auto_bound(self):
        with patch.object(
            pgn,
            "notify_player_group_payment_received",
            new=AsyncMock(),
        ) as notify_mock:
            await pgn.maybe_notify_player_on_auto_bound(
                telegram_chat_id=CHAT_ID,
                amount_cents=5000,
                auto_bound=False,
            )
        notify_mock.assert_not_awaited()

    async def test_skips_when_chat_id_missing(self):
        with patch.object(
            pgn,
            "notify_player_group_payment_received",
            new=AsyncMock(),
        ) as notify_mock:
            await pgn.maybe_notify_player_on_auto_bound(
                telegram_chat_id=None,
                amount_cents=5000,
                auto_bound=True,
            )
        notify_mock.assert_not_awaited()

    async def test_notifies_on_auto_bound(self):
        with patch.object(
            pgn,
            "notify_player_group_payment_received",
            new=AsyncMock(return_value=True),
        ) as notify_mock:
            await pgn.maybe_notify_player_on_auto_bound(
                telegram_chat_id=CHAT_ID,
                amount_cents=5000,
                auto_bound=True,
            )
        notify_mock.assert_awaited_once_with(
            telegram_chat_id=CHAT_ID,
            amount_cents=5000,
            is_test=False,
        )


if __name__ == "__main__":
    unittest.main()
