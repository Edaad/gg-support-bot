"""Tests for /add-triggered bonus recording with Continue button."""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.ext import ApplicationHandlerStop

from bot.handlers import bonus as bonus_mod
from bot.services.bonus_drafts import BonusDraftContext
from bot.services.bonus_from_add import maybe_start_bonus_recording_from_add
from bot.services.bonus_player_resolve import BonusPlayerContext


def _sample_player_ctx(*, title: str = "CC / 8190-5287 / Jacob") -> BonusPlayerContext:
    return BonusPlayerContext(
        group_title=title,
        gg_player_id="8190-5287",
        club_id=1,
        chat_id=-123,
        player_details_id=10,
        zapier_name=title,
    )


class TestMaybeStartBonusRecordingFromAdd(unittest.IsolatedAsyncioTestCase):
    async def test_no_op_without_bonus_amount(self) -> None:
        bot = AsyncMock()
        with patch("bot.services.bonus_from_add.get_db") as mock_get_db:
            await maybe_start_bonus_recording_from_add(
                bot,
                staff_user_id=100,
                club_id=1,
                chat_id=-123,
                group_title="CC / 8190-5287 / Jacob",
                bonus_amount=None,
            )
        mock_get_db.assert_not_called()
        bot.send_message.assert_not_called()

    @patch("bot.services.bonus_from_add.resolve_bonus_player", return_value=None)
    async def test_invalid_title_skips_draft(self, _resolve) -> None:
        bot = AsyncMock()
        with patch("bot.services.bonus_from_add.get_db") as mock_get_db:
            await maybe_start_bonus_recording_from_add(
                bot,
                staff_user_id=100,
                club_id=1,
                chat_id=-123,
                group_title="bad title",
                bonus_amount=Decimal("50"),
            )
        mock_get_db.assert_not_called()

    @patch("bot.services.bonus_from_add.notify_staff_bonus_draft", new_callable=AsyncMock, return_value=True)
    @patch("bot.services.bonus_from_add.create_draft")
    @patch("bot.services.bonus_from_add.resolve_bonus_player", return_value=_sample_player_ctx())
    @patch("bot.services.bonus_from_add.get_db")
    async def test_creates_draft_and_notifies(
        self,
        mock_get_db,
        _resolve,
        mock_create_draft,
        mock_notify,
    ) -> None:
        session = MagicMock()
        mock_get_db.return_value.__enter__ = MagicMock(return_value=session)
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)
        draft = MagicMock()
        draft.id = 7
        draft.club_id = 1
        draft.group_title = "CC / 8190-5287 / Jacob"
        draft.telegram_chat_id = -123
        draft.gg_player_id = "8190-5287"
        draft.player_details_id = 10
        draft.amount = Decimal("50")
        mock_create_draft.return_value = draft

        bot = AsyncMock()
        await maybe_start_bonus_recording_from_add(
            bot,
            staff_user_id=100,
            club_id=1,
            chat_id=-123,
            group_title="CC / 8190-5287 / Jacob",
            bonus_amount=Decimal("50"),
        )

        mock_create_draft.assert_called_once()
        kwargs = mock_create_draft.call_args.kwargs
        self.assertEqual(kwargs["gg_player_id"], "8190-5287")
        self.assertEqual(kwargs["player_details_id"], 10)
        mock_notify.assert_awaited_once_with(
            bot,
            staff_user_id=100,
            draft_id=7,
            group_title="CC / 8190-5287 / Jacob",
            amount=Decimal("50"),
            gg_player_id="8190-5287",
        )


def _callback_update(*, data: str, user_id: int = 555):
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        message=SimpleNamespace(chat=SimpleNamespace(id=user_id)),
    )
    user = SimpleNamespace(id=user_id)
    return SimpleNamespace(
        callback_query=query,
        effective_user=user,
    )


class TestBonusDraftContinue(unittest.IsolatedAsyncioTestCase):
    @patch.object(bonus_mod, "_club_name_for_id", return_value="Round Table")
    @patch.object(bonus_mod, "resolve_bonus_player", return_value=_sample_player_ctx())
    @patch.object(bonus_mod, "_type_keyboard_markup")
    @patch.object(bonus_mod, "get_pending_draft")
    @patch.object(bonus_mod, "get_db")
    async def test_continue_prefills_type_step(
        self,
        mock_get_db,
        mock_get_pending,
        mock_keyboard,
        _resolve,
        _club_name,
    ) -> None:
        session = MagicMock()
        mock_get_db.return_value.__enter__ = MagicMock(return_value=session)
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)
        draft = MagicMock()
        mock_get_pending.return_value = draft

        draft_ctx = BonusDraftContext(
            id=3,
            club_id=1,
            group_title="CC / 8190-5287 / Jacob",
            telegram_chat_id=-1,
            gg_player_id="8190-5287",
            player_details_id=10,
            amount=Decimal("50"),
        )

        with patch.object(bonus_mod, "draft_to_context", return_value=draft_ctx):
            update = _callback_update(data="bonus_draft:3", user_id=555)
            context = SimpleNamespace(
                user_data={},
                job_queue=None,
            )

            with self.assertRaises(ApplicationHandlerStop):
                await bonus_mod.bonus_draft_continue_handler(update, context)

        self.assertEqual(context.user_data["bonus_step"], "type")
        self.assertEqual(context.user_data["bonus_gg_player_id"], "8190-5287")
        self.assertEqual(context.user_data["bonus_amount"], Decimal("50"))
        self.assertEqual(context.user_data["bonus_club_id"], 1)
        self.assertEqual(context.user_data["bonus_draft_id"], 3)
        update.callback_query.edit_message_text.assert_awaited_once()
        mock_keyboard.assert_called_once()

    @patch.object(bonus_mod, "resolve_bonus_player", return_value=None)
    @patch.object(bonus_mod, "_club_name_for_id", return_value="Round Table")
    @patch.object(bonus_mod, "get_pending_draft")
    @patch.object(bonus_mod, "get_db")
    async def test_continue_fails_when_title_unresolvable(
        self,
        mock_get_db,
        mock_get_pending,
        _club_name,
        _resolve,
    ) -> None:
        session = MagicMock()
        mock_get_db.return_value.__enter__ = MagicMock(return_value=session)
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)
        draft = MagicMock()
        mock_get_pending.return_value = draft

        draft_ctx = BonusDraftContext(
            id=4,
            club_id=1,
            group_title="bad",
            telegram_chat_id=-1,
            gg_player_id=None,
            player_details_id=None,
            amount=Decimal("50"),
        )

        with patch.object(bonus_mod, "draft_to_context", return_value=draft_ctx):
            update = _callback_update(data="bonus_draft:4", user_id=555)
            context = SimpleNamespace(user_data={}, job_queue=None)

            with self.assertRaises(ApplicationHandlerStop):
                await bonus_mod.bonus_draft_continue_handler(update, context)

        update.callback_query.edit_message_text.assert_awaited_with(
            "Could not resolve player from group title. Send /bonus to start again."
        )


class TestBonusActorPermissions(unittest.IsolatedAsyncioTestCase):
    async def test_non_admin_actor_allowed_when_admin_id_matches(self) -> None:
        update = SimpleNamespace(effective_user=SimpleNamespace(id=999))
        context = SimpleNamespace(user_data={"bonus_admin_id": 999, "bonus_step": "group_title"})
        self.assertTrue(bonus_mod._is_bonus_actor(update, context))

    @patch.object(bonus_mod, "_club_name_for_id", return_value="Club CC")
    @patch.object(bonus_mod, "resolve_bonus_player", return_value=_sample_player_ctx())
    @patch.object(bonus_mod, "_type_keyboard_markup", return_value=MagicMock())
    async def test_message_handler_accepts_staff_actor(self, _keyboard, _resolve, _club) -> None:
        update = SimpleNamespace(
            message=SimpleNamespace(
                text="CC / 8190-5287 / Jacob",
                reply_text=AsyncMock(),
                chat=SimpleNamespace(type="private"),
            ),
            effective_chat=SimpleNamespace(type="private"),
            effective_user=SimpleNamespace(id=999),
        )
        context = SimpleNamespace(
            user_data={
                "bonus_step": "group_title",
                "bonus_admin_id": 999,
                "bonus_amount": Decimal("50"),
            }
        )

        with self.assertRaises(ApplicationHandlerStop):
            await bonus_mod.bonus_message_handler(update, context)

        self.assertEqual(context.user_data["bonus_step"], "type")
        update.message.reply_text.assert_awaited()


class TestBonusPrefilledClubFinalize(unittest.IsolatedAsyncioTestCase):
    @patch.object(bonus_mod, "_finalize_bonus_record", new_callable=AsyncMock)
    async def test_type_chosen_skips_club_when_prefilled(self, mock_finalize) -> None:
        update = _callback_update(data="btype:2", user_id=555)
        context = SimpleNamespace(
            user_data={
                "bonus_step": "type",
                "bonus_admin_id": 555,
                "bonus_club_id": 1,
            }
        )

        with patch.object(bonus_mod, "_get_bonus_types", return_value=[{"id": 2, "name": "Deposit Match"}]):
            with self.assertRaises(ApplicationHandlerStop):
                await bonus_mod.bonus_callback_handler(update, context)

        mock_finalize.assert_awaited_once()
        self.assertEqual(context.user_data["bonus_type_name"], "Deposit Match")


if __name__ == "__main__":
    unittest.main()
