"""Tests for admin /refresh and Heroku dyno restart."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.refresh import refresh_handler
from bot.services import heroku_restart as hr
from config import ADMIN_USER_IDS

ADMIN_ID = ADMIN_USER_IDS[0]
NON_ADMIN_ID = 999999999


def _make_update(*, user_id: int, chat_type: str = "private", args: list[str] | None = None):
    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat = MagicMock()
    update.effective_chat.type = chat_type
    context = MagicMock()
    context.args = args if args is not None else []
    return update, context


class HerokuRestartServiceTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env_patch = patch.dict(
            os.environ,
            {
                hr.HEROKU_API_KEY_ENV: "test-api-key",
                hr.HEROKU_APP_NAME_ENV: "gg-support-bot-2025",
            },
            clear=False,
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()

    async def test_restart_all_dynos_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 202
        mock_resp.text = ""
        mock_resp.reason_phrase = "Accepted"

        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("bot.services.heroku_restart.httpx.AsyncClient", return_value=mock_client):
            app_name = await hr.restart_all_dynos(triggered_by_user_id=ADMIN_ID)

        self.assertEqual(app_name, "gg-support-bot-2025")
        mock_client.delete.assert_awaited_once()
        call_kwargs = mock_client.delete.await_args
        self.assertIn("gg-support-bot-2025/dynos", call_kwargs.args[0])

    async def test_restart_all_dynos_missing_api_key(self):
        with patch.dict(os.environ, {hr.HEROKU_API_KEY_ENV: ""}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                await hr.restart_all_dynos()
        self.assertIn("HEROKU_API_KEY", str(ctx.exception))

    async def test_restart_all_dynos_api_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        mock_resp.reason_phrase = "Forbidden"

        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("bot.services.heroku_restart.httpx.AsyncClient", return_value=mock_client):
            with self.assertRaises(RuntimeError) as ctx:
                await hr.restart_all_dynos()
        self.assertIn("403", str(ctx.exception))


class RefreshHandlerTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_non_admin_silent(self):
        update, context = _make_update(user_id=NON_ADMIN_ID, args=["confirm"])
        with patch(
            "bot.handlers.refresh.restart_all_dynos",
            new=AsyncMock(),
        ) as mock_restart:
            await refresh_handler(update, context)
        mock_restart.assert_not_awaited()
        update.message.reply_text.assert_not_awaited()

    async def test_group_chat_rejected(self):
        update, context = _make_update(user_id=ADMIN_ID, chat_type="group", args=["confirm"])
        with patch(
            "bot.handlers.refresh.restart_all_dynos",
            new=AsyncMock(),
        ) as mock_restart:
            await refresh_handler(update, context)
        mock_restart.assert_not_awaited()
        update.message.reply_text.assert_awaited_once()
        self.assertIn("DM", update.message.reply_text.await_args.args[0])

    async def test_without_confirm_shows_usage(self):
        update, context = _make_update(user_id=ADMIN_ID, args=[])
        with patch(
            "bot.handlers.refresh.restart_all_dynos",
            new=AsyncMock(),
        ) as mock_restart:
            await refresh_handler(update, context)
        mock_restart.assert_not_awaited()
        update.message.reply_text.assert_awaited_once()
        self.assertIn("confirm", update.message.reply_text.await_args.args[0])

    async def test_confirm_restarts_after_reply(self):
        update, context = _make_update(user_id=ADMIN_ID, args=["confirm"])
        mock_restart = AsyncMock(return_value="gg-support-bot-2025")

        with (
            patch.dict(
                os.environ,
                {hr.HEROKU_APP_NAME_ENV: "gg-support-bot-2025"},
                clear=False,
            ),
            patch("bot.handlers.refresh.restart_all_dynos", new=mock_restart),
        ):
            await refresh_handler(update, context)

        self.assertEqual(update.message.reply_text.await_count, 1)
        self.assertIn("Restarting", update.message.reply_text.await_args.args[0])
        mock_restart.assert_awaited_once_with(triggered_by_user_id=ADMIN_ID)

    async def test_missing_app_name_before_restart(self):
        update, context = _make_update(user_id=ADMIN_ID, args=["confirm"])
        with patch.dict(os.environ, {hr.HEROKU_APP_NAME_ENV: ""}, clear=False):
            with patch(
                "bot.handlers.refresh.restart_all_dynos",
                new=AsyncMock(),
            ) as mock_restart:
                await refresh_handler(update, context)
        mock_restart.assert_not_awaited()
        update.message.reply_text.assert_awaited_once()
        self.assertIn("HEROKU_APP_NAME", update.message.reply_text.await_args.args[0])

    async def test_restart_failure_sends_error(self):
        update, context = _make_update(user_id=ADMIN_ID, args=["confirm"])
        mock_restart = AsyncMock(side_effect=RuntimeError("HEROKU_API_KEY is not set"))

        with (
            patch.dict(
                os.environ,
                {hr.HEROKU_APP_NAME_ENV: "gg-support-bot-2025"},
                clear=False,
            ),
            patch("bot.handlers.refresh.restart_all_dynos", new=mock_restart),
        ):
            await refresh_handler(update, context)

        self.assertEqual(update.message.reply_text.await_count, 2)
        self.assertIn("Restarting", update.message.reply_text.await_args_list[0].args[0])
        self.assertIn("failed", update.message.reply_text.await_args_list[1].args[0].lower())


if __name__ == "__main__":
    unittest.main()
