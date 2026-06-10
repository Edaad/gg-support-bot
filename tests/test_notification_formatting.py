"""Tests for payment notification group-chat hyperlink formatting."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from notification import formatting as nf
from notification.formatting import format_group_chat_line, format_player_id_line


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
    def test_preresolved_invite_url_ignored(self, _mock):
        text = format_group_chat_line(
            group_title="GTO / 5155 / Player",
            telegram_chat_id=-5287778428,
            group_chat_url="https://t.me/+InviteHash",
        )
        self.assertEqual(text, "Group Chat: GTO / 5155 / Player")
        self.assertNotIn("<a href=", text)

    @patch.object(nf, "linked_group_chat_hyperlinks_enabled", return_value=True)
    def test_legacy_basic_id_stays_plain_text(self, _mock):
        text = format_group_chat_line(
            group_title="GTO / 5155-8843 / Over80vpip",
            telegram_chat_id=-5287778428,
            group_chat_url="https://t.me/c/5287778428",
        )
        self.assertEqual(text, "Group Chat: GTO / 5155-8843 / Over80vpip")
        self.assertNotIn("<a href=", text)


class FormatPlayerIdLineTestCase(unittest.TestCase):
    def test_parses_from_group_title(self):
        line = format_player_id_line("RT / 6485-8168 / Angus Mcgoon")
        self.assertEqual(line, "Player ID: <code>6485-8168</code>")

    def test_missing_title_returns_none(self):
        self.assertIsNone(format_player_id_line(None))
        self.assertIsNone(format_player_id_line(""))

    def test_unparseable_title_returns_none(self):
        self.assertIsNone(format_player_id_line("CC / / John"))


if __name__ == "__main__":
    unittest.main()
