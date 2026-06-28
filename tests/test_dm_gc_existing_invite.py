"""Tests for /gc existing-group invite refresh (no stale DB fallback)."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.mtproto_dm_gc_listener import _flow_existing_group


class TestFlowExistingGroupInvite(unittest.IsolatedAsyncioTestCase):
    async def test_does_not_reuse_stale_db_link_when_export_fails(self) -> None:
        client = MagicMock()
        client.get_entity = AsyncMock(return_value=MagicMock())
        client.send_message = AsyncMock()

        cfg = MagicMock(club_key="round_table")
        row = MagicMock(
            id=1,
            telegram_chat_id=-100123,
            invite_link="https://t.me/+stale",
        )
        player = MagicMock()
        player.id = 42
        player.username = "player1"
        player.first_name = "Test"
        player.last_name = "Player"

        with (
            patch(
                "bot.services.mtproto_dm_gc_listener.ensure_player_in_support_group",
                new_callable=AsyncMock,
                return_value="invite_failed",
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener.export_invite_link_for_peer",
                new_callable=AsyncMock,
                return_value=None,
            ) as mock_export,
            patch(
                "bot.services.group_chat_invite_links.export_invite_link_via_bot_api",
                new_callable=AsyncMock,
                return_value=(None, "export_failed"),
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener.update_support_group_chat_row"
            ) as mock_update,
        ):
            await _flow_existing_group(
                client, cfg, row, player, listener_label="@rt"
            )

        mock_export.assert_awaited_once()
        self.assertTrue(mock_export.await_args.kwargs.get("revoke_previous"))
        sent = client.send_message.await_args.args[1]
        self.assertNotIn("https://t.me/+stale", sent)
        self.assertIn("invite link unavailable", sent)
        mock_update.assert_called_once()
        self.assertIsNone(mock_update.call_args.kwargs.get("invite_link"))

    async def test_uses_fresh_exported_link(self) -> None:
        client = MagicMock()
        client.get_entity = AsyncMock(return_value=MagicMock())
        client.send_message = AsyncMock()

        cfg = MagicMock(club_key="round_table")
        row = MagicMock(
            id=2,
            telegram_chat_id=-100456,
            invite_link="https://t.me/+stale",
        )
        player = MagicMock()
        player.id = 99
        player.username = None
        player.first_name = "A"
        player.last_name = "B"

        with (
            patch(
                "bot.services.mtproto_dm_gc_listener.ensure_player_in_support_group",
                new_callable=AsyncMock,
                return_value="invited_ok",
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener.export_invite_link_for_peer",
                new_callable=AsyncMock,
                return_value="https://t.me/+fresh",
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener.update_support_group_chat_row"
            ) as mock_update,
        ):
            await _flow_existing_group(
                client, cfg, row, player, listener_label="@rt"
            )

        sent = client.send_message.await_args.args[1]
        self.assertIn("https://t.me/+fresh", sent)
        self.assertNotIn("https://t.me/+stale", sent)
        mock_update.assert_called_once()
        self.assertEqual(
            mock_update.call_args.kwargs.get("invite_link"),
            "https://t.me/+fresh",
        )


if __name__ == "__main__":
    unittest.main()
