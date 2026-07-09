"""Tests for /report bot handler (group stub + auth)."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Chat, Message, Update, User
from telegram.constants import ChatType

from bot.handlers.issue_reports import _report_group_stub, escalate_entry, report_entry


def _group_update(user_id: int = 100, chat_id: int = -123) -> Update:
    user = User(id=user_id, is_bot=False, first_name="AM")
    chat = Chat(id=chat_id, type=ChatType.SUPERGROUP, title="RT AT / 3333-3333 / @jz034")
    message = Message(
        message_id=1,
        date=None,
        chat=chat,
        from_user=user,
        text="/escalate",
    )
    update = Update(update_id=1, message=message)
    update._effective_user = user
    update._effective_chat = chat
    return update


class TestReportGroupStub(unittest.IsolatedAsyncioTestCase):
    @patch("bot.handlers.issue_reports.notify_staff_issue_report_draft", new_callable=AsyncMock)
    @patch("bot.handlers.issue_reports.create_draft")
    @patch("bot.handlers.issue_reports.get_db")
    @patch("bot.handlers.issue_reports.is_club_staff", return_value=True)
    @patch("bot.handlers.issue_reports.get_club_for_chat", return_value=1)
    async def test_silent_group_stub(
        self,
        _club,
        _staff,
        mock_get_db,
        mock_create_draft,
        mock_notify,
    ) -> None:
        session = MagicMock()
        mock_get_db.return_value.__enter__ = MagicMock(return_value=session)
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)
        draft = MagicMock()
        draft.id = 9
        draft.group_title = "RT AT / 3333-3333 / @jz034"
        mock_create_draft.return_value = draft

        update = _group_update()
        context = MagicMock()
        context.bot.delete_message = AsyncMock()

        await _report_group_stub(update, context)

        context.bot.delete_message.assert_awaited_once()
        mock_notify.assert_awaited_once()

    @patch("bot.handlers.issue_reports.get_club_for_chat", return_value=None)
    async def test_unlinked_group_does_nothing(self, _club) -> None:
        update = _group_update()
        context = MagicMock()
        context.bot.delete_message = AsyncMock()
        await _report_group_stub(update, context)
        context.bot.delete_message.assert_not_awaited()


class TestEscalateEntry(unittest.IsolatedAsyncioTestCase):
    @patch("bot.handlers.issue_reports._report_group_stub", new_callable=AsyncMock)
    @patch("bot.handlers.issue_reports._can_use_issue_reports", return_value=True)
    async def test_group_delegates_to_stub(self, _can, mock_stub) -> None:
        update = _group_update()
        context = MagicMock()
        await escalate_entry(update, context)
        mock_stub.assert_awaited_once()


class TestReportEntry(unittest.IsolatedAsyncioTestCase):
    @patch("bot.handlers.issue_reports._begin_dm_report_flow", new_callable=AsyncMock, return_value=0)
    @patch("bot.handlers.issue_reports._can_use_issue_reports", return_value=True)
    async def test_dm_starts_wizard(self, _can, mock_begin) -> None:
        from telegram.ext import ConversationHandler

        user = User(id=100, is_bot=False, first_name="AM")
        chat = Chat(id=100, type=ChatType.PRIVATE)
        message = Message(message_id=1, date=None, chat=chat, from_user=user, text="/report")
        update = Update(update_id=1, message=message)
        update._effective_user = user
        update._effective_chat = chat
        context = MagicMock()
        context.user_data = {}
        result = await report_entry(update, context)
        self.assertEqual(result, 0)
        mock_begin.assert_awaited_once()

    @patch("bot.handlers.issue_reports._report_group_stub", new_callable=AsyncMock)
    async def test_group_ignored(self, mock_stub) -> None:
        from telegram.ext import ConversationHandler

        update = _group_update()
        context = MagicMock()
        result = await report_entry(update, context)
        self.assertEqual(result, ConversationHandler.END)
        mock_stub.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
