"""Tests for payment notification group-chat hyperlink formatting."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from notification import formatting as nf
from notification.formatting import format_group_chat_line


class FormatGroupChatLineTestCase(unittest.TestCase):
    @patch.object(nf, "linked_group_chat_hyperlinks_enabled", return_value=True)
    def test_enabled_by_default_hyperlinks_supergroup(self, _mock):
        text = format_group_chat_line(
            group_title="RT / 1234 / Player",
            telegram_chat_id=-1001234567890,
        )
        self.assertIn('<a href="https://t.me/c/1234567890">', text)

    @patch.object(nf, "linked_group_chat_hyperlinks_enabled", return_value=False)
    def test_disabled_stays_plain_text(self, _mock):
        text = format_group_chat_line(
            group_title="RT / 1234 / Player",
            telegram_chat_id=-1001234567890,
            group_chat_url="https://t.me/c/1234567890",
        )
        self.assertEqual(text, "Group Chat: RT / 1234 / Player")

    @patch.object(nf, "linked_group_chat_hyperlinks_enabled", return_value=True)
    def test_supergroup_gets_t_me_c_link(self, _mock):
        text = format_group_chat_line(
            group_title="RT / 1234 / Player",
            telegram_chat_id=-1001234567890,
        )
        self.assertEqual(
            text,
            'Group Chat: <a href="https://t.me/c/1234567890">RT / 1234 / Player</a>',
        )

    @patch.object(nf, "linked_group_chat_hyperlinks_enabled", return_value=True)
    def test_preresolved_group_chat_url(self, _mock):
        text = format_group_chat_line(
            group_title="GTO / 5155 / Player",
            telegram_chat_id=-5287778428,
            group_chat_url="https://t.me/+InviteHash",
        )
        self.assertEqual(
            text,
            'Group Chat: <a href="https://t.me/+InviteHash">GTO / 5155 / Player</a>',
        )

    @patch.object(nf, "linked_group_chat_hyperlinks_enabled", return_value=True)
    @patch(
        "bot.services.support_group_chats.fetch_invite_link_for_chat",
        return_value="https://t.me/+InviteHash",
    )
    def test_basic_group_falls_back_to_invite_link(self, mock_fetch, _mock_hyperlinks):
        text = format_group_chat_line(
            group_title="GTO / 5155-8843 / Over80vpip",
            telegram_chat_id=-5287778428,
        )
        self.assertEqual(
            text,
            'Group Chat: <a href="https://t.me/+InviteHash">GTO / 5155-8843 / Over80vpip</a>',
        )
        mock_fetch.assert_called_once_with(
            -5287778428,
            group_title="GTO / 5155-8843 / Over80vpip",
        )

    @patch.object(nf, "linked_group_chat_hyperlinks_enabled", return_value=True)
    @patch(
        "bot.services.support_group_chats.fetch_invite_link_for_chat",
        return_value=None,
    )
    def test_basic_group_without_invite_link_stays_plain_text(self, _mock_fetch, _mock_hyperlinks):
        text = format_group_chat_line(
            group_title="GTO / 5155-8843 / Over80vpip",
            telegram_chat_id=-5287778428,
        )
        self.assertEqual(text, "Group Chat: GTO / 5155-8843 / Over80vpip")


if __name__ == "__main__":
    unittest.main()
