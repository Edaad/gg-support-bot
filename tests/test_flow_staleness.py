"""Tests for deposit/cashout update-age staleness and actor gating."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from bot.handlers import deposit as dep
from bot.handlers import flow_staleness as fs


def _message_update(
    *,
    age_seconds: float,
    text: str = "40",
    chat_id: int = -1003978131309,
    user_id: int = 7516419496,
    chat_type: str = "supergroup",
):
    now = datetime.now(timezone.utc)
    msg_date = now - timedelta(seconds=age_seconds)
    chat = SimpleNamespace(id=chat_id, type=chat_type, title="GTO / 4523-7293 / Ashley")
    user = SimpleNamespace(id=user_id)
    message = SimpleNamespace(
        text=text,
        date=msg_date,
        reply_text=AsyncMock(),
        chat=chat,
    )
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=chat,
        effective_user=user,
    )


def _callback_update(*, age_seconds: float, chat_id: int = -1003978131309):
    now = datetime.now(timezone.utc)
    msg_date = now - timedelta(seconds=age_seconds)
    chat = SimpleNamespace(id=chat_id, type="supergroup")
    message = SimpleNamespace(chat=chat, date=msg_date, message_id=99)
    query = SimpleNamespace(
        data="dep:29",
        message=message,
        answer=AsyncMock(),
        from_user=SimpleNamespace(id=7516419496),
        edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(
        callback_query=query,
        effective_message=message,
        effective_chat=chat,
    )


class TestFlowStalenessHelpers(unittest.TestCase):
    def test_looks_like_amount(self):
        self.assertTrue(fs.looks_like_amount("40"))
        self.assertTrue(fs.looks_like_amount("$100.50"))
        self.assertTrue(fs.looks_like_amount("0"))
        self.assertFalse(fs.looks_like_amount("40 debit card"))
        self.assertFalse(fs.looks_like_amount("It keeps saying link expired"))
        self.assertFalse(fs.looks_like_amount("abc"))

    def test_is_update_too_old_rejects_four_minute_backlog(self):
        update = _message_update(age_seconds=240)
        now = datetime.now(timezone.utc)
        self.assertTrue(fs.is_update_too_old(update, now=now))

    def test_is_update_too_old_accepts_deploy_window_command(self):
        update = _message_update(age_seconds=20)
        now = datetime.now(timezone.utc)
        self.assertFalse(fs.is_update_too_old(update, now=now))

    def test_is_update_too_old_respects_env_override(self):
        update = _message_update(age_seconds=90)
        now = datetime.now(timezone.utc)
        with patch.dict(os.environ, {"BOT_UPDATE_MAX_AGE_SECONDS": "120"}):
            self.assertFalse(fs.is_update_too_old(update, now=now))
        with patch.dict(os.environ, {"BOT_UPDATE_MAX_AGE_SECONDS": "60"}):
            self.assertTrue(fs.is_update_too_old(update, now=now))


class TestDepositEntryStaleness(unittest.IsolatedAsyncioTestCase):
    async def test_stale_deposit_entry_is_silent(self):
        update = _message_update(age_seconds=240, text="/deposit")
        context = SimpleNamespace(chat_data={}, user_data={})
        with patch.object(dep, "get_club_for_chat", return_value=4):
            result = await dep.deposit_entry(update, context)
        self.assertEqual(result, ConversationHandler.END)
        update.message.reply_text.assert_not_called()

    @patch.object(dep, "get_club_simple_mode", return_value=None)
    @patch.object(dep, "_cancel_deposit_reminder")
    @patch.object(dep, "update_group_name")
    @patch.object(dep, "_ask_deposit_amount", new_callable=AsyncMock, return_value=dep.DEPOSIT_AMOUNT)
    @patch.object(dep, "get_club_allows_admin_commands", return_value=True)
    @patch.object(dep, "get_club_for_chat", return_value=4)
    @patch.object(dep, "ADMIN_USER_IDS", {7516419496})
    @patch.object(dep, "is_test_bot_worker", return_value=False)
    async def test_fresh_deposit_entry_still_starts(self, *_mocks):
        update = _message_update(age_seconds=20, text="/deposit")
        context = SimpleNamespace(chat_data={}, user_data={})
        result = await dep.deposit_entry(update, context)
        self.assertEqual(result, dep.DEPOSIT_AMOUNT)
        dep._ask_deposit_amount.assert_awaited_once()


class TestDepositAmountActorGating(unittest.IsolatedAsyncioTestCase):
    def _admin_context(self):
        return SimpleNamespace(
            chat_data={
                "deposit_club_id": 4,
                "deposit_chat_id": -1003978131309,
                "deposit_admin_initiated": True,
                "deposit_admin_user_id": 7516419496,
                "deposit_awaiting_amount": True,
            },
            user_data={},
        )

    async def test_group_chatter_is_silent(self):
        update = _message_update(
            age_seconds=5,
            text="It keeps saying link expired",
            user_id=8132930521,
        )
        context = self._admin_context()
        result = await dep.deposit_amount_received(update, context)
        self.assertEqual(result, dep.DEPOSIT_AMOUNT)
        update.message.reply_text.assert_not_called()

    @patch.object(dep, "_prompt_deposit_methods", new_callable=AsyncMock)
    @patch.object(dep, "is_round_table_club", return_value=False)
    @patch.object(
        dep,
        "get_methods_for_amount",
        return_value=[{"id": 29, "slug": "applepay", "name": "Apple Pay"}],
    )
    async def test_admin_amount_still_accepted(self, *_mocks):
        update = _message_update(age_seconds=5, text="40", user_id=7516419496)
        context = self._admin_context()
        result = await dep.deposit_amount_received(update, context)
        self.assertEqual(result, dep.DEPOSIT_CHOOSE)
        self.assertEqual(context.chat_data["deposit_amount"], Decimal("40"))

    async def test_stale_amount_update_is_silent(self):
        update = _message_update(age_seconds=240, text="40", user_id=7516419496)
        context = self._admin_context()
        result = await dep.deposit_amount_received(update, context)
        self.assertEqual(result, ConversationHandler.END)
        update.message.reply_text.assert_not_called()


class TestDepositCallbackStaleness(unittest.IsolatedAsyncioTestCase):
    @patch.object(dep, "get_method_by_id", return_value={"id": 29, "name": "Apple Pay", "slug": "applepay", "has_sub_options": False})
    @patch.object(dep, "_run_normal_deposit_from_choice", new_callable=AsyncMock, return_value=ConversationHandler.END)
    async def test_stale_method_callback_rejected(self, *_mocks):
        update = _callback_update(age_seconds=240)
        context = SimpleNamespace(
            chat_data={
                "deposit_club_id": 4,
                "deposit_amount": Decimal("40"),
            },
            user_data={},
        )
        result = await dep.deposit_method_chosen(update, context)
        self.assertEqual(result, ConversationHandler.END)
        update.callback_query.answer.assert_awaited_once()
        self.assertIn("expired", update.callback_query.answer.await_args.args[0].lower())
        dep._run_normal_deposit_from_choice.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
