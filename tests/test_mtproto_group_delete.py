"""Tests for MTProto /delete confirm helpers."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telethon.tl.types import Channel, Chat, User

from bot.services.mtproto_group_delete import (
    _kick_all_basic_chat_participants,
    _resolve_club_id_for_delete,
    parse_delete_confirm_command,
)


class TestParseDeleteConfirmCommand(unittest.TestCase):
    def test_exact_match(self) -> None:
        self.assertTrue(parse_delete_confirm_command("/delete confirm"))
        self.assertTrue(parse_delete_confirm_command("/DELETE CONFIRM"))
        self.assertTrue(parse_delete_confirm_command("  /delete confirm  "))

    def test_bot_suffix(self) -> None:
        self.assertTrue(parse_delete_confirm_command("/delete@SomeBot confirm"))

    def test_rejects_bare_or_extra(self) -> None:
        self.assertFalse(parse_delete_confirm_command("/delete"))
        self.assertFalse(parse_delete_confirm_command("/delete confirm extra"))
        self.assertFalse(parse_delete_confirm_command("/add confirm"))


class TestResolveClubIdForDelete(unittest.TestCase):
    @patch("bot.services.mtproto_group_delete.fetch_support_group_chat_row_for_chat")
    @patch("bot.services.mtproto_group_delete.get_club_for_chat")
    def test_groups_table_match(
        self, mock_get_club, mock_fetch_row
    ) -> None:
        cfg = MagicMock(club_key="round_table", link_club_id=2)
        mock_get_club.return_value = 2

        self.assertEqual(_resolve_club_id_for_delete(-100123, cfg), 2)
        mock_fetch_row.assert_not_called()

    @patch("bot.services.mtproto_group_delete.fetch_support_group_chat_row_for_chat")
    @patch("bot.services.mtproto_group_delete.get_club_for_chat")
    def test_support_group_fallback(
        self, mock_get_club, mock_fetch_row
    ) -> None:
        cfg = MagicMock(club_key="round_table", link_club_id=2)
        mock_get_club.return_value = None
        row = MagicMock(club_key="round_table")
        mock_fetch_row.return_value = row

        self.assertEqual(_resolve_club_id_for_delete(-5287778428, cfg), 2)

    @patch("bot.services.mtproto_group_delete.fetch_support_group_chat_row_for_chat")
    @patch("bot.services.mtproto_group_delete.get_club_for_chat")
    def test_wrong_club_rejected(
        self, mock_get_club, mock_fetch_row
    ) -> None:
        cfg = MagicMock(club_key="round_table", link_club_id=2)
        mock_get_club.return_value = 99
        mock_fetch_row.return_value = None

        self.assertIsNone(_resolve_club_id_for_delete(-100123, cfg))


class TestKickBasicChatParticipants(unittest.IsolatedAsyncioTestCase):
    async def test_removes_other_users(self) -> None:
        chat = MagicMock(spec=Chat)
        chat.id = 4242

        keep = MagicMock(spec=User)
        keep.id = 1
        drop = MagicMock(spec=User)
        drop.id = 2

        async def iter_participants(_entity):
            for user in (keep, drop):
                yield user

        client = AsyncMock()
        client.iter_participants = iter_participants

        async def flood_retry_side_effect(_label, fn):
            return await fn()

        with patch(
            "bot.services.mtproto_group_delete._with_single_flood_retry",
            new=AsyncMock(side_effect=flood_retry_side_effect),
        ):
            kicked, failed = await _kick_all_basic_chat_participants(
                client, chat, self_id=1
            )

        self.assertEqual(kicked, 1)
        self.assertEqual(failed, 0)
        client.assert_called()


if __name__ == "__main__":
    unittest.main()
