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


def _referred_player_ctx(
    *,
    title: str = "RT / 1111-2222 / Friend",
    chat_id: int | None = -999,
) -> BonusPlayerContext:
    return BonusPlayerContext(
        group_title=title,
        gg_player_id="1111-2222",
        club_id=2,
        chat_id=chat_id,
        player_details_id=20,
        zapier_name=title,
    )


def _callback_update(*, data: str, user_id: int = 555):
    chat = SimpleNamespace(id=user_id, send_message=AsyncMock())
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        message=SimpleNamespace(chat=chat),
    )
    user = SimpleNamespace(id=user_id)
    return SimpleNamespace(
        callback_query=query,
        effective_user=user,
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


class TestBonusReferralFlow(unittest.IsolatedAsyncioTestCase):
    @patch.object(bonus_mod, "_club_name_for_id", return_value="Club CC")
    @patch.object(bonus_mod, "_finalize_bonus_record", new_callable=AsyncMock)
    async def test_non_referral_club_chosen_finalizes(self, mock_finalize, _club) -> None:
        update = _callback_update(data="bclub:1")
        context = SimpleNamespace(
            user_data={
                "bonus_step": "club",
                "bonus_admin_id": 555,
                "bonus_type_name": "First Deposit",
            }
        )

        with self.assertRaises(ApplicationHandlerStop):
            await bonus_mod.bonus_callback_handler(update, context)

        mock_finalize.assert_awaited_once()

    @patch.object(bonus_mod, "_club_name_for_id", return_value="Club CC")
    async def test_referral_club_chosen_asks_referred_title(self, _club) -> None:
        update = _callback_update(data="bclub:1")
        context = SimpleNamespace(
            user_data={
                "bonus_step": "club",
                "bonus_admin_id": 555,
                "bonus_type_name": "Referral",
            }
        )

        with self.assertRaises(ApplicationHandlerStop):
            await bonus_mod.bonus_callback_handler(update, context)

        self.assertEqual(context.user_data["bonus_step"], "referred_group_title")
        update.callback_query.message.chat.send_message.assert_awaited_once_with(
            bonus_mod._REFERRED_GROUP_TITLE_PROMPT
        )

    @patch.object(bonus_mod, "_finalize_bonus_record", new_callable=AsyncMock)
    @patch.object(bonus_mod, "resolve_bonus_player")
    async def test_referred_title_sets_metadata_and_finalizes(
        self, mock_resolve, mock_finalize
    ) -> None:
        title = "RT / 1111-2222 / Friend"
        mock_resolve.return_value = _referred_player_ctx(title=title)
        update = _private_text_update(text=title)
        context = SimpleNamespace(
            user_data={
                "bonus_step": "referred_group_title",
                "bonus_admin_id": 12345,
                "bonus_gg_player_id": "8190-5287",
                "bonus_chat_id": -123,
            }
        )

        with self.assertRaises(ApplicationHandlerStop):
            await bonus_mod.bonus_message_handler(update, context)

        mock_resolve.assert_called_once_with(group_title=title, chat_id=None)
        self.assertEqual(
            context.user_data["bonus_metadata"],
            {
                "referred_player_gc_id": -999,
                "referred_player_group_title": title,
            },
        )
        mock_finalize.assert_awaited_once()

    @patch.object(bonus_mod, "_finalize_bonus_record", new_callable=AsyncMock)
    @patch.object(bonus_mod, "resolve_bonus_player")
    async def test_referred_title_allows_null_chat_id(
        self, mock_resolve, mock_finalize
    ) -> None:
        title = "RT / 1111-2222 / Friend"
        mock_resolve.return_value = _referred_player_ctx(title=title, chat_id=None)
        update = _private_text_update(text=title)
        context = SimpleNamespace(
            user_data={
                "bonus_step": "referred_group_title",
                "bonus_admin_id": 12345,
                "bonus_gg_player_id": "8190-5287",
            }
        )

        with self.assertRaises(ApplicationHandlerStop):
            await bonus_mod.bonus_message_handler(update, context)

        self.assertEqual(
            context.user_data["bonus_metadata"]["referred_player_gc_id"],
            None,
        )
        mock_finalize.assert_awaited_once()

    @patch.object(bonus_mod, "_finalize_bonus_record", new_callable=AsyncMock)
    @patch.object(bonus_mod, "resolve_bonus_player", return_value=None)
    async def test_invalid_referred_title_rejected(self, _resolve, mock_finalize) -> None:
        update = _private_text_update(text="bad title")
        context = SimpleNamespace(
            user_data={
                "bonus_step": "referred_group_title",
                "bonus_admin_id": 12345,
                "bonus_gg_player_id": "8190-5287",
            }
        )

        with self.assertRaises(ApplicationHandlerStop):
            await bonus_mod.bonus_message_handler(update, context)

        self.assertEqual(context.user_data["bonus_step"], "referred_group_title")
        self.assertNotIn("bonus_metadata", context.user_data)
        mock_finalize.assert_not_called()
        update.message.reply_text.assert_awaited_once()
        self.assertIn("Invalid group title", update.message.reply_text.await_args.args[0])

    @patch.object(bonus_mod, "_finalize_bonus_record", new_callable=AsyncMock)
    @patch.object(bonus_mod, "resolve_bonus_player")
    async def test_same_player_by_gg_id_rejected(self, mock_resolve, mock_finalize) -> None:
        mock_resolve.return_value = _referred_player_ctx()
        update = _private_text_update(text="RT / 1111-2222 / Friend")
        context = SimpleNamespace(
            user_data={
                "bonus_step": "referred_group_title",
                "bonus_admin_id": 12345,
                "bonus_gg_player_id": "1111-2222",
            }
        )

        with self.assertRaises(ApplicationHandlerStop):
            await bonus_mod.bonus_message_handler(update, context)

        mock_finalize.assert_not_called()
        update.message.reply_text.assert_awaited_once_with(bonus_mod._SAME_PLAYER_ERROR)

    @patch.object(bonus_mod, "_finalize_bonus_record", new_callable=AsyncMock)
    @patch.object(bonus_mod, "resolve_bonus_player")
    async def test_same_player_by_chat_id_rejected(self, mock_resolve, mock_finalize) -> None:
        mock_resolve.return_value = _referred_player_ctx(chat_id=-123)
        update = _private_text_update(text="RT / 1111-2222 / Friend")
        context = SimpleNamespace(
            user_data={
                "bonus_step": "referred_group_title",
                "bonus_admin_id": 12345,
                "bonus_gg_player_id": "8190-5287",
                "bonus_chat_id": -123,
            }
        )

        with self.assertRaises(ApplicationHandlerStop):
            await bonus_mod.bonus_message_handler(update, context)

        mock_finalize.assert_not_called()
        update.message.reply_text.assert_awaited_once_with(bonus_mod._SAME_PLAYER_ERROR)

    @patch.object(bonus_mod, "_ask_referred_player", new_callable=AsyncMock)
    @patch.object(bonus_mod, "_get_bonus_types", return_value=[{"id": 2, "name": "Referral"}])
    async def test_referral_type_with_prefilled_club_asks_referred(
        self, _types, mock_ask
    ) -> None:
        update = _callback_update(data="btype:2")
        context = SimpleNamespace(
            user_data={
                "bonus_step": "type",
                "bonus_admin_id": 555,
                "bonus_club_id": 1,
            }
        )

        with self.assertRaises(ApplicationHandlerStop):
            await bonus_mod.bonus_callback_handler(update, context)

        mock_ask.assert_awaited_once()
        self.assertEqual(context.user_data["bonus_type_name"], "Referral")


if __name__ == "__main__":
    unittest.main()
