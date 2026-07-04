"""Tests for /bonus step-based flow."""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.ext import ApplicationHandlerStop

from bot.handlers import bonus as bonus_mod
from bot.handlers.flow_cancel import ACTIVE_FLOW_KEY
from bot.services.bonus_player_resolve import BonusPlayerContext


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


def _sample_player_ctx(*, title: str = "CC / 8190-5287 / Jacob") -> BonusPlayerContext:
    return BonusPlayerContext(
        group_title=title,
        gg_player_id="8190-5287",
        club_id=1,
        chat_id=None,
        player_details_id=10,
        zapier_name=title,
    )


class TestBonusFlow(unittest.IsolatedAsyncioTestCase):
    @patch.object(bonus_mod, "_club_name_for_id", return_value="Club CC")
    @patch.object(bonus_mod, "resolve_bonus_player", return_value=_sample_player_ctx())
    @patch.object(bonus_mod, "_type_keyboard_markup", return_value=MagicMock())
    @patch.object(bonus_mod, "ADMIN_USER_IDS", {12345})
    async def test_group_title_advances_to_amount(self, _keyboard, _resolve, _club):
        update = _private_text_update(text="CC / 8190-5287 / Jacob")
        context = SimpleNamespace(user_data={"bonus_step": "group_title", "bonus_admin_id": 12345})

        with self.assertRaises(ApplicationHandlerStop):
            await bonus_mod.bonus_message_handler(update, context)

        self.assertEqual(context.user_data["bonus_step"], "amount")
        self.assertEqual(context.user_data["bonus_group_title"], "CC / 8190-5287 / Jacob")
        update.message.reply_text.assert_awaited_once_with("Amount ($):")

    @patch.object(bonus_mod, "resolve_bonus_player", return_value=None)
    @patch.object(bonus_mod, "ADMIN_USER_IDS", {12345})
    async def test_invalid_group_title_rejected(self, _resolve):
        update = _private_text_update(text="bad title")
        context = SimpleNamespace(user_data={"bonus_step": "group_title", "bonus_admin_id": 12345})

        with self.assertRaises(ApplicationHandlerStop):
            await bonus_mod.bonus_message_handler(update, context)

        self.assertEqual(context.user_data["bonus_step"], "group_title")
        update.message.reply_text.assert_awaited_once()
        self.assertIn("Invalid group title", update.message.reply_text.await_args.args[0])

    @patch.object(bonus_mod, "_club_name_for_id", return_value="Club CC")
    @patch.object(bonus_mod, "resolve_bonus_player", return_value=_sample_player_ctx())
    @patch.object(bonus_mod, "_type_keyboard_markup", return_value=MagicMock())
    @patch.object(bonus_mod, "ADMIN_USER_IDS", {12345})
    async def test_group_title_not_blocked_by_stale_sendinactive_keys(self, _keyboard, _resolve, _club):
        update = _private_text_update(text="CC / 8190-5287 / Jacob")
        context = SimpleNamespace(
            user_data={
                "bonus_step": "group_title",
                "bonus_admin_id": 12345,
                ACTIVE_FLOW_KEY: "bonus",
                "io_club_key": "round_table",
                "io_step": "compose",
            }
        )

        with self.assertRaises(ApplicationHandlerStop):
            await bonus_mod.bonus_message_handler(update, context)

        self.assertEqual(context.user_data["bonus_step"], "amount")
        self.assertEqual(context.user_data["bonus_gg_player_id"], "8190-5287")

    async def test_message_handler_ignored_when_not_in_bonus_flow(self):
        update = _private_text_update(text="hello")
        context = SimpleNamespace(user_data={})

        await bonus_mod.bonus_message_handler(update, context)
        update.message.reply_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
