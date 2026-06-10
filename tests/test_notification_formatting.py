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
    def test_preresolved_invite_url_ignored_uses_member_link(self, _mock):
        text = format_group_chat_line(
            group_title="GTO / 5155 / Player",
            telegram_chat_id=-5287778428,
            group_chat_url="https://t.me/+InviteHash",
        )
        self.assertIn('<a href="https://t.me/c/5287778428">', text)
        self.assertNotIn("t.me/+", text)

    @patch.object(nf, "linked_group_chat_hyperlinks_enabled", return_value=True)
    def test_preresolved_t_me_c_url_is_used(self, _mock):
        text = format_group_chat_line(
            group_title="GTO / 5155 / Player",
            telegram_chat_id=-5287778428,
            group_chat_url="https://t.me/c/5287778428",
        )
        self.assertEqual(
            text,
            'Group Chat: <a href="https://t.me/c/5287778428">GTO / 5155 / Player</a>',
        )

    @patch.object(nf, "linked_group_chat_hyperlinks_enabled", return_value=True)
    def test_legacy_basic_id_gets_member_link_via_variant(self, _mock):
        text = format_group_chat_line(
            group_title="GTO / 5155-8843 / Over80vpip",
            telegram_chat_id=-5287778428,
        )
        self.assertIn('<a href="https://t.me/c/5287778428">', text)

    @patch.object(nf, "linked_group_chat_hyperlinks_enabled", return_value=True)
    def test_legacy_basic_id_uses_supergroup_variant_for_link(self, _mock):
        text = format_group_chat_line(
            group_title="RT / 1234 / Player",
            telegram_chat_id=-1234567890,
        )
        self.assertEqual(
            text,
            'Group Chat: <a href="https://t.me/c/1234567890">RT / 1234 / Player</a>',
        )


if __name__ == "__main__":
    unittest.main()
