"""Tests for on-demand group chat invite link resolution."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from bot.services import group_chat_invite_links as gcil
from notification.formatting import format_group_chat_line


class ResolveGroupChatNotificationUrlTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_supergroup_skips_bot_api(self):
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

    async def test_db_hit_skips_bot_api(self):
        with (
            patch.object(gcil, "telegram_supergroup_chat_url", return_value=None),
            patch.object(gcil, "fetch_invite_link_for_chat", return_value="https://t.me/+Cached"),
            patch.object(
                gcil,
                "export_invite_link_via_bot_api",
                new=AsyncMock(),
            ) as bot_mock,
        ):
            url = await gcil.resolve_group_chat_notification_url(
                telegram_chat_id=-5287778428,
                group_title="GTO / 5155 / Player",
                club_id=4,
            )
        self.assertEqual(url, "https://t.me/+Cached")
        bot_mock.assert_not_called()

    async def test_bot_api_success_upserts_and_returns_link(self):
        with (
            patch.object(gcil, "telegram_supergroup_chat_url", return_value=None),
            patch.object(gcil, "fetch_invite_link_for_chat", return_value=None),
            patch.object(
                gcil,
                "export_invite_link_via_bot_api",
                new=AsyncMock(return_value=("https://t.me/+Fresh", None)),
            ),
            patch.object(
                gcil,
                "_club_upsert_metadata",
                return_value=("clubgto", "ClubGTO"),
            ),
            patch.object(
                gcil,
                "upsert_support_group_invite_link",
                return_value=("inserted", 99),
            ) as upsert_mock,
        ):
            url = await gcil.resolve_group_chat_notification_url(
                telegram_chat_id=-5287778428,
                group_title="GTO / 5155 / Player",
                club_id=4,
            )
        self.assertEqual(url, "https://t.me/+Fresh")
        upsert_mock.assert_called_once()

    async def test_bot_api_failure_returns_none(self):
        with (
            patch.object(gcil, "telegram_supergroup_chat_url", return_value=None),
            patch.object(gcil, "fetch_invite_link_for_chat", return_value=None),
            patch.object(
                gcil,
                "export_invite_link_via_bot_api",
                new=AsyncMock(return_value=(None, "not_admin")),
            ),
        ):
            url = await gcil.resolve_group_chat_notification_url(
                telegram_chat_id=-5287778428,
                group_title="GTO / 5155 / Player",
                club_id=4,
            )
        self.assertIsNone(url)


class FormatGroupChatLinePreresolvedUrlTestCase(unittest.TestCase):
    def test_preresolved_invite_url_used(self):
        text = format_group_chat_line(
            group_title="GTO / 5155 / Player",
            telegram_chat_id=-5287778428,
            group_chat_url="https://t.me/+OnDemand",
        )
        self.assertIn('href="https://t.me/+OnDemand"', text)

    def test_bot_failure_stays_plain_without_preresolved_url(self):
        with patch(
            "bot.services.support_group_chats.fetch_invite_link_for_chat",
            return_value=None,
        ):
            text = format_group_chat_line(
                group_title="GTO / 5155 / Player",
                telegram_chat_id=-5287778428,
            )
        self.assertEqual(text, "Group Chat: GTO / 5155 / Player")
        self.assertNotIn("<a href=", text)


if __name__ == "__main__":
    unittest.main()
