"""Tests for Telegram chat id URL helpers used in payment notifications."""

from __future__ import annotations

import unittest

from notification.chat_id import telegram_chat_id_variants, telegram_supergroup_chat_url


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


class TelegramChatIdVariantsTestCase(unittest.TestCase):
    def test_supergroup_legacy_variant_pair(self):
        variants = telegram_chat_id_variants(-1001234567890)
        self.assertIn(-1001234567890, variants)
        self.assertIn(-1234567890, variants)


if __name__ == "__main__":
    unittest.main()
