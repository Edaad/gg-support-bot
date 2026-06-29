"""Tests for bind_player_for_gc_reuse /bind rebind behavior."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bot.services.support_group_chats import bind_player_for_gc_reuse


class BindPlayerGcReuseTests(unittest.TestCase):
    @patch("bot.services.support_group_chats.update_support_group_chat_row")
    @patch("bot.services.support_group_chats.fetch_support_group_chat_by_telegram_chat_id")
    @patch("bot.services.support_group_chats.fetch_support_group_chat_by_club_player")
    def test_rebind_updates_chat_id_when_allowed(
        self,
        mock_by_player,
        mock_by_chat,
        mock_update,
    ) -> None:
        mock_by_player.return_value = SimpleNamespace(id=2959, telegram_chat_id=-1004325999602)
        mock_by_chat.return_value = None
        mock_update.return_value = (True, None)

        status, row_id = bind_player_for_gc_reuse(
            club_key="round_table",
            club_display_name="Round Table",
            telegram_chat_id=-5529522447,
            telegram_chat_title="RT / 7108-8970 / Penner77",
            player_telegram_user_id=7876709437,
            player_username="Penner77",
            allow_player_rebind=True,
        )

        self.assertEqual(status, "rebound")
        self.assertEqual(row_id, 2959)
        mock_update.assert_called_once_with(
            2959,
            telegram_chat_id=-5529522447,
            telegram_chat_title="RT / 7108-8970 / Penner77",
            player_username="Penner77",
            player_display_name=None,
            player_dm_status="bind_rebound",
            last_error_message="",
        )

    @patch("bot.services.support_group_chats.fetch_support_group_chat_by_club_player")
    def test_player_bound_elsewhere_without_rebind(self, mock_by_player) -> None:
        mock_by_player.return_value = SimpleNamespace(id=2959, telegram_chat_id=-1004325999602)

        status, row_id = bind_player_for_gc_reuse(
            club_key="round_table",
            club_display_name="Round Table",
            telegram_chat_id=-5529522447,
            telegram_chat_title="RT / 7108-8970 / Penner77",
            player_telegram_user_id=7876709437,
        )

        self.assertEqual(status, "player_bound_elsewhere")
        self.assertEqual(row_id, 2959)

    @patch("bot.services.support_group_chats.fetch_support_group_chat_by_telegram_chat_id")
    @patch("bot.services.support_group_chats.fetch_support_group_chat_by_club_player")
    def test_rebind_blocked_when_target_chat_bound_to_other_player(
        self,
        mock_by_player,
        mock_by_chat,
    ) -> None:
        mock_by_player.return_value = SimpleNamespace(id=2959, telegram_chat_id=-1004325999602)
        mock_by_chat.return_value = SimpleNamespace(
            id=4000,
            player_telegram_user_id=999,
        )

        status, row_id = bind_player_for_gc_reuse(
            club_key="round_table",
            club_display_name="Round Table",
            telegram_chat_id=-5529522447,
            telegram_chat_title="RT / 7108-8970 / Penner77",
            player_telegram_user_id=7876709437,
            allow_player_rebind=True,
        )

        self.assertEqual(status, "chat_other_player")
        self.assertEqual(row_id, 4000)


if __name__ == "__main__":
    unittest.main()
