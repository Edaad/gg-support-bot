"""Tests for fileid vs issue report evidence step."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import PhotoSize

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
        photo = PhotoSize(file_id="fid", file_unique_id="u", width=1, height=1)
        update = MagicMock()
        update.message = MagicMock()
        update.message.photo = [photo]
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.user_data = {
            "active_bot_flow": "issue_report",
            "ir_details": "broken",
            "ir_evidence": [],
        }
        await fileid_photo_handler(update, context)
        update.message.reply_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
