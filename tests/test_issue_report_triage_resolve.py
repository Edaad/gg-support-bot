"""Tests for /reports ID resolve triage follow-up."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Chat, Update, User
from telegram.constants import ChatType
from telegram.ext import ApplicationHandlerStop, ConversationHandler

from bot.handlers.issue_reports import (
    _TRIAGE_MODE_RESOLVE_EVIDENCE,
    _TRIAGE_MODE_RESOLVE_NOTES,
    _prepare_triage_flow,
    get_report_conversation_handler,
    reports_handler,
    triage_followup_message,
    triage_followup_priority,
)


def _dm_update(
    text: str,
    *,
    user_id: int = 100,
    args: list[str] | None = None,
) -> tuple[Update, MagicMock]:
    user = User(id=user_id, is_bot=False, first_name="Admin")
    chat = Chat(id=user_id, type=ChatType.PRIVATE)
    message = MagicMock()
    message.text = text
    message.reply_text = AsyncMock()
    update = Update(update_id=1, message=message)
    update._effective_user = user
    update._effective_chat = chat
    context = MagicMock()
    context.args = args or []
    context.user_data = {}
    return update, context


class TestPrepareTriageFlow(unittest.TestCase):
    def test_ends_active_report_conversation(self) -> None:
        import bot.handlers.issue_reports as mod

        conv = get_report_conversation_handler()
        mod._report_conversation = conv
        update, context = _dm_update("/reports 67 resolve")
        key = conv._get_key(update)
        conv._conversations[key] = 0
        context.user_data["ir_title"] = "stale"
        context.user_data["active_bot_flow"] = "issue_report"

        _prepare_triage_flow(update, context)

        self.assertNotIn(key, conv._conversations)
        self.assertNotIn("ir_title", context.user_data)
        self.assertNotIn("active_bot_flow", context.user_data)


class TestReportsResolveCommand(unittest.IsolatedAsyncioTestCase):
    @patch("bot.handlers.issue_reports._can_use_issue_reports", return_value=True)
    @patch("bot.handlers.issue_reports.get_db")
    async def test_starts_resolve_flow(self, mock_get_db, _mock_can) -> None:
        report = MagicMock()
        report.status = "open"
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        mock_get_db.return_value = session

        with patch(
            "bot.handlers.issue_reports.get_issue_report",
            return_value=report,
        ):
            update, context = _dm_update(
                "/reports 67 resolve",
                args=["67", "resolve"],
            )
            context.bot = MagicMock()
            await reports_handler(update, context)

            update.message.reply_text.assert_awaited_once()
        self.assertEqual(context.user_data["ir_triage_report_id"], 67)
        self.assertEqual(
            context.user_data["ir_triage_mode"],
            _TRIAGE_MODE_RESOLVE_NOTES,
        )


class TestTriageFollowupResolve(unittest.IsolatedAsyncioTestCase):
    @patch("bot.handlers.issue_reports._can_use_issue_reports", return_value=True)
    async def test_notes_advance_to_evidence_step(self, _mock_can) -> None:
        update, context = _dm_update("Restarted the worker")
        context.user_data["ir_triage_mode"] = _TRIAGE_MODE_RESOLVE_NOTES
        context.user_data["ir_triage_report_id"] = 67

        await triage_followup_message(update, context)

        update.message.reply_text.assert_awaited_once()
        self.assertEqual(context.user_data["ir_resolve_notes"], "Restarted the worker")
        self.assertEqual(
            context.user_data["ir_triage_mode"],
            _TRIAGE_MODE_RESOLVE_EVIDENCE,
        )

    @patch("bot.handlers.issue_reports._can_use_issue_reports", return_value=True)
    async def test_priority_handler_stops_propagation(self, _mock_can) -> None:
        update, context = _dm_update("Restarted the worker")
        context.user_data["ir_triage_mode"] = _TRIAGE_MODE_RESOLVE_NOTES
        context.user_data["ir_triage_report_id"] = 67
        with self.assertRaises(ApplicationHandlerStop):
            await triage_followup_priority(update, context)


if __name__ == "__main__":
    unittest.main()
