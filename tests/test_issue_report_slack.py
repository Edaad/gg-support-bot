"""Tests for issue report Slack formatting and notify."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.slack_ops_format import beautify_slack_body
from bot.services.slack_ops_notify import notify_slack_issue_report


class TestIssueReportSlackFormat(unittest.TestCase):
    def test_beautifies_issue_report_with_notify_and_details(self) -> None:
        body = "\n".join(
            [
                "Issue report",
                "",
                "Ticket: #3",
                "Title: Cashout stuck",
                "Notify: Head admin, Engineer",
                "Reporter: Alice (source=api)",
                "Group: RT / 1234-5678 / player",
                "",
                "Details:",
                "Player cannot cash out",
            ]
        )
        text = beautify_slack_body(body, source="issue_report")
        self.assertIn("*Title:* Cashout stuck", text)
        self.assertIn("*For:* Head admin, Engineer", text)
        self.assertIn("*Group:* RT / 1234-5678 / player", text)
        self.assertIn("*Details:*", text)
        self.assertIn("Player cannot cash out", text)

    def test_beautifies_legacy_description_block(self) -> None:
        body = "\n".join(
            [
                "Issue report",
                "",
                "Ticket: #3",
                "Title: Cashout stuck",
                "Reporter: Alice (source=api)",
                "Tags: cashout, deposit",
                "",
                "Description:",
                "Player cannot cash out",
            ]
        )
        text = beautify_slack_body(body, source="issue_report")
        self.assertIn("`cashout`", text)
        self.assertIn("Player cannot cash out", text)


class TestNotifySlackIssueReport(unittest.IsolatedAsyncioTestCase):
    @patch.dict(os.environ, {}, clear=True)
    async def test_no_op_when_unconfigured(self) -> None:
        ok, ts, file_ids = await notify_slack_issue_report(
            "Issue report\n\nTicket: #1",
            tags=["head_admin"],
            file_bytes=[("a.png", b"x", "image/png")],
        )
        self.assertFalse(ok)
        self.assertIsNone(ts)
        self.assertEqual(file_ids, [None])

    @patch.dict(
        os.environ,
        {
            "SLACK_ISSUE_REPORT_BOT_TOKEN": "xoxb-test",
            "SLACK_ISSUE_REPORT_CHANNEL_ID": "C123",
            "ISSUE_REPORT_TAG_MENTIONS": '{"head_admin": "<@U999>"}',
        },
    )
    @patch("bot.services.slack_ops_notify.httpx.AsyncClient")
    async def test_posts_message_and_uploads_files(
        self, mock_client_cls: MagicMock
    ) -> None:
        msg_resp = MagicMock()
        msg_resp.status_code = 200
        msg_resp.raise_for_status = MagicMock()
        msg_resp.json.return_value = {"ok": True, "ts": "1234.5678"}

        file_resp = MagicMock()
        file_resp.status_code = 200
        file_resp.raise_for_status = MagicMock()
        file_resp.json.return_value = {"ok": True, "file": {"id": "F001"}}

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(side_effect=[msg_resp, file_resp])
        mock_client_cls.return_value = mock_client

        ok, ts, file_ids = await notify_slack_issue_report(
            "\n".join(
                [
                    "Issue report",
                    "",
                    "Ticket: #5",
                    "Title: Test",
                    "Notify: Head admin",
                    "Reporter: Bob (source=api)",
                    "",
                    "Details:",
                    "Details here",
                ]
            ),
            tags=["head_admin"],
            file_bytes=[("shot.png", b"png", "image/png")],
        )

        self.assertTrue(ok)
        self.assertEqual(ts, "1234.5678")
        self.assertEqual(file_ids, ["F001"])
        self.assertEqual(mock_client.post.await_count, 2)
        message_call = mock_client.post.await_args_list[0]
        self.assertIn("<@U999>", message_call.kwargs["json"]["text"])
        self.assertEqual(message_call.kwargs["json"]["channel"], "C123")


if __name__ == "__main__":
    unittest.main()
