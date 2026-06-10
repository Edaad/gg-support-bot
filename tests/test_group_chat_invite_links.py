"""Tests for on-demand group chat invite link resolution."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from bot.services import group_chat_invite_links as gcil
from notification.formatting import format_group_chat_line


class ResolveGroupChatNotificationUrlTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_supergroup_returns_t_me_c(self):
        with patch.object(
            gcil,
            "export_invite_link_via_bot_api",
            new=AsyncMock(),
        ) as bot_mock:
            url = await gcil.resolve_group_chat_notification_url(
                telegram_chat_id=-1001234567890,
                group_title="RT / 1234 / Player",
                club_id=2,
            )
        self.assertEqual(url, "https://t.me/c/1234567890")
        bot_mock.assert_not_called()

    async def test_legacy_basic_id_returns_supergroup_variant_url(self):
        url = await gcil.resolve_group_chat_notification_url(
            telegram_chat_id=-1234567890,
            group_title="RT / 1234 / Player",
            club_id=2,
        )
        self.assertEqual(url, "https://t.me/c/1234567890")

    async def test_legacy_basic_id_returns_member_link_not_invite(self):
        with patch.object(
            gcil,
            "export_invite_link_via_bot_api",
            new=AsyncMock(return_value=("https://t.me/+Cached", None)),
        ) as bot_mock:
            url = await gcil.resolve_group_chat_notification_url(
                telegram_chat_id=-5287778428,
                group_title="GTO / 5155 / Player",
                club_id=4,
            )
        self.assertEqual(url, "https://t.me/c/5287778428")
        bot_mock.assert_not_called()

    async def test_empty_title_returns_none(self):
        url = await gcil.resolve_group_chat_notification_url(
            telegram_chat_id=-1001234567890,
            group_title="  ",
            club_id=2,
        )
        self.assertIsNone(url)


class FormatGroupChatLinePreresolvedUrlTestCase(unittest.TestCase):
    @patch("notification.formatting.linked_group_chat_hyperlinks_enabled", return_value=False)
    def test_preresolved_invite_url_ignored_when_hyperlinks_disabled(self, _mock):
        text = format_group_chat_line(
            group_title="GTO / 5155 / Player",
            telegram_chat_id=-5287778428,
            group_chat_url="https://t.me/+OnDemand",
        )
        self.assertEqual(text, "Group Chat: GTO / 5155 / Player")
        self.assertNotIn("<a href=", text)

    def test_invalid_chat_id_stays_plain_without_preresolved_url(self):
        text = format_group_chat_line(
            group_title="GTO / 5155 / Player",
            telegram_chat_id=12345,
        )
        self.assertEqual(text, "Group Chat: GTO / 5155 / Player")
        self.assertNotIn("<a href=", text)


if __name__ == "__main__":
    unittest.main()
