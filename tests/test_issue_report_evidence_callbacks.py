"""Tests for /report evidence-step Done/Cancel callbacks."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from telegram import Chat, User
from telegram.constants import ChatType
from telegram.ext import ApplicationHandlerStop, ConversationHandler

from bot.handlers.issue_reports import (
    IR_CONFIRM,
    get_report_conversation_handler,
    report_evidence_priority,
)


def _callback_update(data: str, *, user_id: int = 100) -> tuple[MagicMock, MagicMock]:
    user = User(id=user_id, is_bot=False, first_name="Admin")
    chat = Chat(id=user_id, type=ChatType.PRIVATE)
    message = MagicMock()
    message.message_id = 9
    message.chat = chat
    message.text = "Saved (1/5)"
    message.reply_text = AsyncMock()
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = message
    update = MagicMock()
    update.callback_query = query
    update.effective_user = user
    update.effective_chat = chat
    context = MagicMock()
    context.user_data = {
        "active_bot_flow": "issue_report",
        "ir_details": "OCR mismatch on deposit",
        "ir_evidence": [MagicMock()],
        "ir_title": "Deposit uncertain",
        "ir_notify_tags": ["head_admin"],
        "ir_admin_id": user_id,
    }
    return update, context


class TestReportEvidencePriority(unittest.IsolatedAsyncioTestCase):
    async def test_done_advances_even_without_conv_state(self) -> None:
        import bot.handlers.issue_reports as mod

        conv = get_report_conversation_handler()
        mod._report_conversation = conv
        update, context = _callback_update("ir_evidence_done")
        query = update.callback_query

        with self.assertRaises(ApplicationHandlerStop):
            await report_evidence_priority(update, context)

        query.answer.assert_awaited_once()
        query.edit_message_text.assert_awaited_once()
        key = conv._get_key(update)
        self.assertEqual(conv._conversations.get(key), IR_CONFIRM)

    async def test_cancel_clears_flow_without_conv_state(self) -> None:
        import bot.handlers.issue_reports as mod

        conv = get_report_conversation_handler()
        mod._report_conversation = conv
        update, context = _callback_update("ir_cancel")
        query = update.callback_query

        with self.assertRaises(ApplicationHandlerStop):
            await report_evidence_priority(update, context)

        key = conv._get_key(update)
        self.assertNotIn(key, conv._conversations)
        self.assertNotIn("ir_details", context.user_data)

    async def test_ignored_outside_evidence_step(self) -> None:
        update, context = _callback_update("ir_evidence_done")
        context.user_data = {"active_bot_flow": "issue_report", "ir_title": "x"}

        await report_evidence_priority(update, context)


if __name__ == "__main__":
    unittest.main()
