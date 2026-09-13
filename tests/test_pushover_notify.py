"""Tests for Pushover notify helper."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import pushover_notify as push


class NotifyPushoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_skips_when_env_unset(self) -> None:
        with patch.dict(
            "os.environ",
            {
                push.PUSHOVER_APP_TOKEN_ENV: "",
                push.PUSHOVER_USER_KEY_ENV: "",
            },
            clear=False,
        ), patch("bot.services.pushover_notify.httpx.AsyncClient") as client_cls:
            ok = await push.notify_pushover("hello", source="test")
        self.assertFalse(ok)
        client_cls.assert_not_called()

    async def test_success_posts_form_and_returns_true(self) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"status": 1, "request": "req-1"}
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(
            "os.environ",
            {
                push.PUSHOVER_APP_TOKEN_ENV: "tok123",
                push.PUSHOVER_USER_KEY_ENV: "usr456",
            },
            clear=False,
        ), patch(
            "bot.services.pushover_notify.httpx.AsyncClient",
            return_value=client,
        ):
            ok = await push.notify_pushover(
                "Player waiting",
                title="URGENT cashout",
                url="https://dash.example/cashout-records/1",
                url_title="Open cashout",
                priority=1,
                source="cashout_slack_reminder",
            )

        self.assertTrue(ok)
        client.post.assert_awaited_once()
        args, kwargs = client.post.await_args
        self.assertEqual(args[0], push.PUSHOVER_MESSAGES_URL)
        data = kwargs["data"]
        self.assertEqual(data["token"], "tok123")
        self.assertEqual(data["user"], "usr456")
        self.assertEqual(data["message"], "Player waiting")
        self.assertEqual(data["title"], "URGENT cashout")
        self.assertEqual(data["priority"], 1)
        self.assertEqual(data["url"], "https://dash.example/cashout-records/1")
        self.assertEqual(data["url_title"], "Open cashout")

    async def test_4xx_returns_false(self) -> None:
        resp = MagicMock()
        resp.status_code = 400
        resp.text = '{"status":0,"errors":["user identifier is invalid"]}'
        resp.json.return_value = {
            "status": 0,
            "errors": ["user identifier is invalid"],
        }
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(
            "os.environ",
            {
                push.PUSHOVER_APP_TOKEN_ENV: "tok123",
                push.PUSHOVER_USER_KEY_ENV: "bad",
            },
            clear=False,
        ), patch(
            "bot.services.pushover_notify.httpx.AsyncClient",
            return_value=client,
        ):
            ok = await push.notify_pushover("hello", source="test")

        self.assertFalse(ok)

    async def test_empty_message_returns_false(self) -> None:
        with patch("bot.services.pushover_notify.httpx.AsyncClient") as client_cls:
            ok = await push.notify_pushover("   ", source="test")
        self.assertFalse(ok)
        client_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
