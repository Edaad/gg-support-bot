"""Tests for /bonus step-based flow."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ApplicationHandlerStop

from bot.handlers import bonus as bonus_mod


def _private_text_update(*, text: str, user_id: int = 12345, chat_id: int = 12345):
    chat = SimpleNamespace(id=chat_id, type="private")
    user = SimpleNamespace(id=user_id)
    message = SimpleNamespace(
        text=text,
        reply_text=AsyncMock(),
        chat=chat,
    )
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=chat,
        effective_user=user,
    )


class TestBonusFlow(unittest.IsolatedAsyncioTestCase):
    @patch.object(bonus_mod, "ADMIN_USER_IDS", {12345})
    async def test_username_advances_to_amount(self):
        update = _private_text_update(text="TakeYourStack")
        context = SimpleNamespace(user_data={"bonus_step": "username", "bonus_admin_id": 12345})

        with self.assertRaises(ApplicationHandlerStop):
            await bonus_mod.bonus_message_handler(update, context)

        self.assertEqual(context.user_data["bonus_step"], "amount")
        self.assertEqual(context.user_data["bonus_player"], "TakeYourStack")
        update.message.reply_text.assert_awaited_once_with("Amount ($):")

    async def test_message_handler_ignored_when_not_in_bonus_flow(self):
        update = _private_text_update(text="hello")
        context = SimpleNamespace(user_data={})

        await bonus_mod.bonus_message_handler(update, context)
        update.message.reply_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
