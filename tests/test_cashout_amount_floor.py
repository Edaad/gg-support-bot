"""Cashout amounts with cents are floored to whole dollars before claiming chips."""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import cashout as co
from bot.services.mtproto_group_cash import parse_cash_command


class ParseCashCommandTests(unittest.TestCase):
    def test_cents_are_floored(self) -> None:
        self.assertEqual(parse_cash_command("/cash 50.5"), Decimal("50"))
        self.assertEqual(parse_cash_command("/cash $1,200.99"), Decimal("1200"))

    def test_whole_amount_unchanged(self) -> None:
        self.assertEqual(parse_cash_command("/cash 500"), Decimal("500"))

    def test_under_a_dollar_is_rejected(self) -> None:
        self.assertIsNone(parse_cash_command("/cash 0.75"))


class AutoCashoutAmountTests(unittest.IsolatedAsyncioTestCase):
    async def _send(self, text):
        message = SimpleNamespace(text=text, reply_text=AsyncMock())
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=555),
            effective_chat=SimpleNamespace(id=-100),
        )
        context = SimpleNamespace(
            chat_data={"cashout_club_id": 7, "cashout_chat_id": -100},
            user_data={},
        )
        eligible = patch.object(co, "_auto_eligible_methods", return_value=[{"id": 1}])
        patches = [
            patch.object(co, "is_update_too_old", return_value=False),
            patch.object(co, "cashout_amount_actor_allowed", return_value=True),
            patch(
                "bot.services.support_group_idle_episode.mark_expected_flow_input",
                return_value=None,
            ),
            patch.object(co, "get_cashout_max_amount", return_value=None),
            patch.object(co, "_cashout_unions_for_flow", return_value=None),
            patch.object(co, "_auto_show_methods", new=AsyncMock(return_value="SHOW")),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        with eligible as mock_eligible:
            state = await co.cashout_auto_amount_received(update, context)
        return state, context, message, mock_eligible

    async def test_cents_are_floored_before_the_claim(self) -> None:
        state, context, _message, mock_eligible = await self._send("50.5")
        self.assertEqual(state, "SHOW")
        self.assertEqual(context.chat_data["cashout_amount"], Decimal("50"))
        self.assertEqual(mock_eligible.call_args.args[2], Decimal("50"))

    async def test_under_a_dollar_asks_again(self) -> None:
        state, context, message, _mock = await self._send("0.50")
        self.assertEqual(state, co.CASHOUT_AUTO_AMOUNT)
        self.assertNotIn("cashout_amount", context.chat_data)
        message.reply_text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
