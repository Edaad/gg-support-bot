"""Tests for Heroku release-phase deploy admin notifications."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import deploy_notify as dn
from config import ADMIN_USER_IDS


class DeployNotifyCooldownTestCase(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()

    def test_should_notify_when_table_empty(self):
        self.session.execute.return_value.first.return_value = None
        with patch.object(dn, "is_deploy_notify_enabled", return_value=True):
            self.assertTrue(dn.should_notify_deploy(self.session))

    def test_should_notify_when_last_notified_null(self):
        self.session.execute.return_value.first.return_value = (None,)
        with patch.object(dn, "is_deploy_notify_enabled", return_value=True):
            self.assertTrue(dn.should_notify_deploy(self.session))

    def test_should_not_notify_within_cooldown(self):
        recent = datetime.now(timezone.utc) - timedelta(minutes=30)
        self.session.execute.return_value.first.return_value = (recent,)
        with patch.object(dn, "is_deploy_notify_enabled", return_value=True):
            with patch.object(dn, "cooldown_seconds", return_value=3600):
                self.assertFalse(dn.should_notify_deploy(self.session))

    def test_should_notify_after_cooldown(self):
        old = datetime.now(timezone.utc) - timedelta(minutes=90)
        self.session.execute.return_value.first.return_value = (old,)
        with patch.object(dn, "is_deploy_notify_enabled", return_value=True):
            with patch.object(dn, "cooldown_seconds", return_value=3600):
                self.assertTrue(dn.should_notify_deploy(self.session))

    def test_should_not_notify_when_disabled(self):
        with patch.object(dn, "is_deploy_notify_enabled", return_value=False):
            self.assertFalse(dn.should_notify_deploy(self.session))
        self.session.execute.assert_not_called()

    def test_record_deploy_notify_upserts(self):
        dn.record_deploy_notify(self.session)
        self.session.execute.assert_called_once()

    def test_is_deploy_notify_enabled_defaults_true(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(dn.is_deploy_notify_enabled())

    def test_is_deploy_notify_enabled_false_values(self):
        for value in ("0", "false", "no", "off"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {dn.DEPLOY_NOTIFY_ENABLED_ENV: value}, clear=False):
                    self.assertFalse(dn.is_deploy_notify_enabled())


class DeployNotifySendTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_notify_all_admin_user_ids_sends_to_each_admin(self):
        mock_bot = AsyncMock()
        mock_bot.initialize = AsyncMock()
        mock_bot.shutdown = AsyncMock()
        mock_bot.send_message = AsyncMock()

        with patch.dict(os.environ, {dn.TELEGRAM_BOT_TOKEN_ENV: "test-token"}, clear=False):
            with patch("bot.services.deploy_notify.Bot", return_value=mock_bot):
                sent = await dn.notify_all_admin_user_ids("Deploy warning")

        self.assertEqual(sent, len(ADMIN_USER_IDS))
        self.assertEqual(mock_bot.send_message.await_count, len(ADMIN_USER_IDS))
        seen_ids = {
            call.kwargs["chat_id"] for call in mock_bot.send_message.await_args_list
        }
        self.assertEqual(seen_ids, set(int(x) for x in ADMIN_USER_IDS))

    async def test_notify_all_admin_user_ids_counts_partial_failures(self):
        mock_bot = AsyncMock()
        mock_bot.initialize = AsyncMock()
        mock_bot.shutdown = AsyncMock()

        async def _send(*, chat_id, text):
            if chat_id == int(ADMIN_USER_IDS[0]):
                raise RuntimeError("blocked")

        mock_bot.send_message = AsyncMock(side_effect=_send)

        with patch.dict(os.environ, {dn.TELEGRAM_BOT_TOKEN_ENV: "test-token"}, clear=False):
            with patch("bot.services.deploy_notify.Bot", return_value=mock_bot):
                sent = await dn.notify_all_admin_user_ids("Deploy warning")

        self.assertEqual(sent, len(ADMIN_USER_IDS) - 1)

    async def test_notify_all_admin_user_ids_no_token(self):
        with patch.dict(os.environ, {dn.TELEGRAM_BOT_TOKEN_ENV: ""}, clear=False):
            sent = await dn.notify_all_admin_user_ids("Deploy warning")
        self.assertEqual(sent, 0)


if __name__ == "__main__":
    unittest.main()
