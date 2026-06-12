"""Tests for Slack ops notify helper (bot API + webhook)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.slack_ops_notify import (
    format_slack_ops_message,
    notify_slack_ops,
)


class TestFormatSlackOpsMessage(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_source_prefix_only(self) -> None:
        text = format_slack_ops_message("Something failed", source="migration_recovery")
        self.assertIn("*Migration Recovery*", text)
        self.assertIn("Something failed", text)

    @patch.dict(os.environ, {"SLACK_OPS_MENTION": "<@U123>"})
    def test_includes_mention(self) -> None:
        text = format_slack_ops_message("Alert", source="notification_report")
        self.assertTrue(text.startswith("<@U123>"))
        self.assertIn("*Notification Report*", text)
        self.assertIn("Alert", text)

    @patch.dict(os.environ, {}, clear=True)
    def test_truncates_long_body(self) -> None:
        long_body = "x" * 4000
        text = format_slack_ops_message(long_body, source="test")
        self.assertLessEqual(len(text), 3000)
        self.assertTrue(text.endswith("…"))


class TestNotifySlackOps(unittest.IsolatedAsyncioTestCase):
    async def test_no_op_when_unconfigured(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            ok = await notify_slack_ops("hello", source="test")
        self.assertFalse(ok)

    @patch.dict(
        os.environ,
        {
            "SLACK_OPS_BOT_TOKEN": "xoxb-test",
            "SLACK_OPS_CHANNEL_ID": "C123",
        },
    )
    @patch("bot.services.slack_ops_notify.httpx.AsyncClient")
    async def test_posts_via_chat_post_message(self, mock_client_cls: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"ok": True, "ts": "1234.5678"}
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        ok = await notify_slack_ops("failed row", source="migration_recovery")

        self.assertTrue(ok)
        mock_client.post.assert_awaited_once()
        call_args = mock_client.post.await_args
        self.assertEqual(call_args.args[0], "https://slack.com/api/chat.postMessage")
        self.assertEqual(call_args.kwargs["headers"]["Authorization"], "Bearer xoxb-test")
        payload = call_args.kwargs["json"]
        self.assertEqual(payload["channel"], "C123")
        self.assertIn("Migration Recovery", payload["text"])
        self.assertIn("failed row", payload["text"])

    @patch.dict(
        os.environ,
        {
            "SLACK_OPS_BOT_TOKEN": "xoxb-test",
            "SLACK_OPS_CHANNEL_ID": "C123",
            "SLACK_OPS_WEBHOOK_URL": "https://hooks.slack.com/test",
        },
    )
    @patch("bot.services.slack_ops_notify.httpx.AsyncClient")
    async def test_falls_back_to_webhook_when_api_returns_not_ok(
        self, mock_client_cls: MagicMock
    ) -> None:
        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.raise_for_status = MagicMock()
        api_resp.json.return_value = {"ok": False, "error": "channel_not_found"}

        webhook_resp = MagicMock()
        webhook_resp.status_code = 200
        webhook_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(side_effect=[api_resp, webhook_resp])
        mock_client_cls.return_value = mock_client

        ok = await notify_slack_ops("oops", source="test")

        self.assertTrue(ok)
        self.assertEqual(mock_client.post.await_count, 2)
        webhook_call = mock_client.post.await_args_list[1]
        self.assertEqual(webhook_call.args[0], "https://hooks.slack.com/test")

    @patch.dict(os.environ, {"SLACK_OPS_WEBHOOK_URL": "https://hooks.slack.com/test"})
    @patch("bot.services.slack_ops_notify.httpx.AsyncClient")
    async def test_webhook_only_when_bot_unset(self, mock_client_cls: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        ok = await notify_slack_ops("failed row", source="migration_recovery")

        self.assertTrue(ok)
        call_kwargs = mock_client.post.await_args.kwargs
        self.assertIn("Migration Recovery", call_kwargs["json"]["text"])
        self.assertIn("failed row", call_kwargs["json"]["text"])

    @patch.dict(
        os.environ,
        {
            "SLACK_OPS_BOT_TOKEN": "xoxb-test",
            "SLACK_OPS_CHANNEL_ID": "C123",
        },
    )
    @patch("bot.services.slack_ops_notify.httpx.AsyncClient")
    async def test_returns_false_on_http_error(self, mock_client_cls: MagicMock) -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(side_effect=Exception("network"))
        mock_client_cls.return_value = mock_client

        ok = await notify_slack_ops("oops", source="test")

        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
