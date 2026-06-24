"""Tests for fileid vs issue report evidence step."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Chat, Message, PhotoSize, Update, User
from telegram.constants import ChatType

from bot.handlers.issue_reports import issue_report_awaiting_evidence
from bot.handlers.start import fileid_photo_handler


class TestIssueReportAwaitingEvidence(unittest.TestCase):
    def test_true_during_evidence_step(self) -> None:
        context = MagicMock()
        context.user_data = {
            "active_bot_flow": "issue_report",
            "ir_details": "something broke",
            "ir_evidence": [],
        }
        self.assertTrue(issue_report_awaiting_evidence(context))

    def test_false_before_details(self) -> None:
        context = MagicMock()
        context.user_data = {"active_bot_flow": "issue_report", "ir_title": "x"}
        self.assertFalse(issue_report_awaiting_evidence(context))


class TestFileidPhotoHandler(unittest.IsolatedAsyncioTestCase):
    async def test_skips_reply_during_report_evidence(self) -> None:
        user = User(id=1, is_bot=False, first_name="A")
        chat = Chat(id=1, type=ChatType.PRIVATE)
        photo = PhotoSize(file_id="fid", file_unique_id="u", width=1, height=1)
        message = Message(message_id=1, date=None, chat=chat, from_user=user, photo=[photo])
        update = Update(update_id=1, message=message)
        context = MagicMock()
        context.user_data = {
            "active_bot_flow": "issue_report",
            "ir_details": "broken",
            "ir_evidence": [],
        }
        message.reply_text = AsyncMock()
        await fileid_photo_handler(update, context)
        message.reply_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
