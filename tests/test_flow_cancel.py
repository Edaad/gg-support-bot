"""Tests for DM flow mutual exclusion and /cancel reliability."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Chat, User
from telegram.constants import ChatType
from telegram.ext import ApplicationHandlerStop, ConversationHandler

from bot.handlers import issue_reports as ir_mod
from bot.handlers.bonus import bonus_entry
from bot.handlers.flow_cancel import (
    ACTIVE_FLOW_KEY,
    dm_flow_cancel_priority,
    flow_cancel_handler,
    get_active_dm_flow,
    issue_report_flow_active,
)
from bot.handlers.issue_reports import get_report_conversation_handler, report_entry


def _private_command_update(*, command_text: str = "/cancel", user_id: int = 100):
    user = User(id=user_id, is_bot=False, first_name="Admin")
    chat = Chat(id=user_id, type=ChatType.PRIVATE)
    message = MagicMock()
    message.text = command_text
    message.reply_text = AsyncMock()
    message.chat = chat
    update = MagicMock()
    update.message = message
    update.callback_query = None
    update.effective_message = message
    update.effective_user = user
    update.effective_chat = chat
    context = MagicMock()
    context.user_data = {}
    context.chat_data = {}
    return update, context


class TestIssueReportFlowDetection(unittest.TestCase):
    def test_active_with_only_admin_id_and_marker(self) -> None:
        context = MagicMock()
        context.user_data = {
            ACTIVE_FLOW_KEY: "issue_report",
            "ir_admin_id": 100,
        }
        self.assertTrue(issue_report_flow_active(context))

    def test_active_with_wizard_keys(self) -> None:
        context = MagicMock()
        context.user_data = {"ir_title": "Payment bug"}
        self.assertTrue(issue_report_flow_active(context))

    def test_inactive_when_empty(self) -> None:
        context = MagicMock()
        context.user_data = {}
        self.assertFalse(issue_report_flow_active(context))


class TestDmFlowCancel(unittest.IsolatedAsyncioTestCase):
    async def test_priority_cancel_early_report_wizard(self) -> None:
        update, context = _private_command_update()
        context.user_data = {
            ACTIVE_FLOW_KEY: "issue_report",
            "ir_admin_id": 100,
        }

        with self.assertRaises(ApplicationHandlerStop):
            await dm_flow_cancel_priority(update, context)

        update.message.reply_text.assert_awaited_once_with("Issue report cancelled.")
        self.assertNotIn("ir_admin_id", context.user_data)
        self.assertNotIn(ACTIVE_FLOW_KEY, context.user_data)

    async def test_priority_cancel_with_title_but_no_conv_state(self) -> None:
        conv = get_report_conversation_handler()
        ir_mod._report_conversation = conv
        update, context = _private_command_update()
        context.user_data = {
            ACTIVE_FLOW_KEY: "issue_report",
            "ir_admin_id": 100,
            "ir_title": "Payment notification bug",
            "ir_notify_tags": ["head_admin"],
        }
        key = conv._get_key(update)
        self.assertNotIn(key, conv._conversations)

        with self.assertRaises(ApplicationHandlerStop):
            await dm_flow_cancel_priority(update, context)

        update.message.reply_text.assert_awaited_once_with("Issue report cancelled.")
        self.assertNotIn(key, conv._conversations)
        self.assertIsNone(get_active_dm_flow(context))

    async def test_flow_cancel_handler_uses_dm_flow(self) -> None:
        update, context = _private_command_update()
        context.user_data = {
            ACTIVE_FLOW_KEY: "issue_report",
            "ir_admin_id": 100,
            "ir_title": "Bug",
        }

        await flow_cancel_handler(update, context)

        update.message.reply_text.assert_awaited_once_with("Issue report cancelled.")
        self.assertIsNone(get_active_dm_flow(context))

    async def test_flow_cancel_handler_nothing_active(self) -> None:
        update, context = _private_command_update()

        await flow_cancel_handler(update, context)

        update.message.reply_text.assert_awaited_once()
        self.assertIn("No active", update.message.reply_text.await_args.args[0])


class TestCrossFlowBlocking(unittest.IsolatedAsyncioTestCase):
    @patch.object(ir_mod, "_can_use_issue_reports", return_value=True)
    async def test_report_blocked_while_bonus_active(self, _can) -> None:
        update, context = _private_command_update(command_text="/report")
        context.user_data = {
            ACTIVE_FLOW_KEY: "bonus",
            "bonus_step": "amount",
            "bonus_admin_id": 100,
        }

        state = await report_entry(update, context)

        self.assertEqual(state, ConversationHandler.END)
        update.message.reply_text.assert_awaited_once()
        msg = update.message.reply_text.await_args.args[0]
        self.assertIn("/bonus", msg)
        self.assertIn("/cancel", msg)

    @patch.object(ir_mod, "_can_use_issue_reports", return_value=True)
    async def test_report_allowed_after_bonus_cleared(self, _can) -> None:
        update, context = _private_command_update(command_text="/report")
        context.user_data = {}

        with patch.object(ir_mod, "_begin_dm_report_flow", new_callable=AsyncMock) as begin:
            begin.return_value = ir_mod.IR_NOTIFY
            state = await report_entry(update, context)

        self.assertEqual(state, ir_mod.IR_NOTIFY)
        begin.assert_awaited_once()

    @patch("bot.handlers.bonus.ADMIN_USER_IDS", {100})
    async def test_bonus_blocked_while_report_active(self) -> None:
        update, context = _private_command_update(command_text="/bonus")
        context.user_data = {
            ACTIVE_FLOW_KEY: "issue_report",
            "ir_admin_id": 100,
            "ir_title": "In progress",
        }

        with self.assertRaises(ApplicationHandlerStop):
            await bonus_entry(update, context)

        update.message.reply_text.assert_awaited_once()
        msg = update.message.reply_text.await_args.args[0]
        self.assertIn("/report", msg)
        self.assertIn("/cancel", msg)

    @patch("bot.handlers.bonus.ADMIN_USER_IDS", {100})
    async def test_bonus_reentry_blocked(self) -> None:
        update, context = _private_command_update(command_text="/bonus")
        context.user_data = {
            ACTIVE_FLOW_KEY: "bonus",
            "bonus_step": "group_title",
            "bonus_admin_id": 100,
        }

        with self.assertRaises(ApplicationHandlerStop):
            await bonus_entry(update, context)

        update.message.reply_text.assert_awaited_once()
        msg = update.message.reply_text.await_args.args[0]
        self.assertIn("/bonus", msg)
        self.assertIn("/cancel", msg)


if __name__ == "__main__":
    unittest.main()
