"""Tests for Telegram chat id URL helpers used in payment notifications."""

from __future__ import annotations

import unittest

from notification.chat_id import (
    is_joinable_invite_url,
    notification_group_chat_url,
    telegram_chat_id_variants,
    telegram_supergroup_chat_url,
)


class TelegramSupergroupChatUrlTestCase(unittest.TestCase):
    def test_supergroup_id_gets_t_me_c_link(self):
        self.assertEqual(
            telegram_supergroup_chat_url(-1003959356975),
            "https://t.me/c/3959356975",
        )

    def test_basic_group_id_has_no_t_me_c_link(self):
        # Bot API type=group — t.me/c/… does not work for these chats.
        self.assertIsNone(telegram_supergroup_chat_url(-5287778428))
        self.assertIsNone(telegram_supergroup_chat_url(-5201145198))

    def test_positive_id_returns_none(self):
        self.assertIsNone(telegram_supergroup_chat_url(12345))


class IsJoinableInviteUrlTestCase(unittest.TestCase):
    def test_t_me_plus_is_joinable(self):
        self.assertTrue(is_joinable_invite_url("https://t.me/+AbCdEf"))

    def test_joinchat_is_joinable(self):
        self.assertTrue(is_joinable_invite_url("https://t.me/joinchat/AAAA"))

    def test_t_me_c_is_not_joinable(self):
        self.assertFalse(is_joinable_invite_url("https://t.me/c/1234567890"))


class NotificationGroupChatUrlTestCase(unittest.TestCase):
    def test_supergroup_id(self):
        self.assertEqual(
            notification_group_chat_url(-1003959356975),
            "https://t.me/c/3959356975",
        )

    def test_legacy_basic_id_has_no_link(self):
        # Basic groups use unrelated -100… ids after migrate; never synthesize t.me/c.
        self.assertIsNone(notification_group_chat_url(-1234567890))
        self.assertIsNone(notification_group_chat_url(-5287778428))

    def test_positive_id_returns_none(self):
        self.assertIsNone(notification_group_chat_url(12345))


class TelegramChatIdVariantsTestCase(unittest.TestCase):
    def test_supergroup_legacy_variant_pair(self):
        variants = telegram_chat_id_variants(-1001234567890)
        self.assertIn(-1001234567890, variants)
        self.assertIn(-1234567890, variants)


if __name__ == "__main__":
    unittest.main()
