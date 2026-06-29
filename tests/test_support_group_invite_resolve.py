"""Tests for invite link expiry check and /gc refresh."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.group_chat_invite_links import resolve_support_group_invite_link
from bot.services.mtproto_dm_gc_listener import _flow_existing_group


class ResolveSupportGroupInviteLinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_valid_stored_link(self) -> None:
        client = MagicMock()
        peer = MagicMock()
        with (
            patch(
                "bot.services.mtproto_group_join.is_invite_link_valid",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.services.mtproto_group_create.export_invite_link_for_peer",
                new=AsyncMock(),
            ) as mock_export,
        ):
            link, source = await resolve_support_group_invite_link(
                client,
                chat_id=-100123,
                peer=peer,
                stored_link="https://t.me/+valid",
            )
        self.assertEqual(link, "https://t.me/+valid")
        self.assertEqual(source, "stored_valid")
        mock_export.assert_not_awaited()

    async def test_refreshes_expired_link_via_bot_api(self) -> None:
        client = MagicMock()
        peer = MagicMock()
        with (
            patch(
                "bot.services.mtproto_group_join.is_invite_link_valid",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "bot.services.mtproto_group_create.export_invite_link_for_peer",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "bot.services.group_chat_invite_links.export_invite_link_via_bot_api",
                new=AsyncMock(return_value=("https://t.me/+fresh", None)),
            ),
        ):
            link, source = await resolve_support_group_invite_link(
                client,
                chat_id=-100123,
                peer=peer,
                stored_link="https://t.me/+stale",
            )
        self.assertEqual(link, "https://t.me/+fresh")
        self.assertEqual(source, "bot_api_export")


class TestFlowExistingGroupInvite(unittest.IsolatedAsyncioTestCase):
    async def test_does_not_reuse_stale_db_link_when_refresh_fails(self) -> None:
        client = MagicMock()
        client.get_entity = AsyncMock(return_value=MagicMock())
        client.send_message = AsyncMock()

        cfg = MagicMock(club_key="round_table")
        row = MagicMock(
            id=1,
            telegram_chat_id=-100123,
            invite_link="https://t.me/+stale",
            telegram_chat_title="RT / / Test",
        )
        player = MagicMock()
        player.id = 42
        player.username = "player1"
        player.first_name = "Test"
        player.last_name = "Player"

        with (
            patch(
                "bot.services.mtproto_dm_gc_listener.ensure_player_in_support_group",
                new=AsyncMock(return_value="invite_failed"),
            ),
            patch(
                "bot.services.group_chat_invite_links.resolve_support_group_invite_link",
                new=AsyncMock(return_value=(None, "unavailable")),
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener.update_support_group_chat_row"
            ) as mock_update,
        ):
            link = await _flow_existing_group(
                client, cfg, row, player, listener_label="@rt"
            )

        sent = client.send_message.await_args.args[1]
        self.assertNotIn("https://t.me/+stale", sent)
        self.assertIn("invite link unavailable", sent)
        mock_update.assert_called_once()
        self.assertIsNone(mock_update.call_args.kwargs.get("invite_link"))
        self.assertIsNone(link)

    async def test_uses_resolved_link(self) -> None:
        client = MagicMock()
        client.get_entity = AsyncMock(return_value=MagicMock())
        client.send_message = AsyncMock()

        cfg = MagicMock(club_key="round_table")
        row = MagicMock(
            id=2,
            telegram_chat_id=-100456,
            invite_link="https://t.me/+stale",
            telegram_chat_title="RT / / AB",
        )
        player = MagicMock()
        player.id = 99
        player.username = None
        player.first_name = "A"
        player.last_name = "B"

        with (
            patch(
                "bot.services.mtproto_dm_gc_listener.ensure_player_in_support_group",
                new=AsyncMock(return_value="invited_ok"),
            ),
            patch(
                "bot.services.group_chat_invite_links.resolve_support_group_invite_link",
                new=AsyncMock(return_value=("https://t.me/+fresh", "bot_api_export")),
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener.update_support_group_chat_row"
            ) as mock_update,
        ):
            link = await _flow_existing_group(
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
        self.assertEqual(link, "https://t.me/+fresh")

    async def test_outgoing_gc_sends_one_message_on_success(self) -> None:
        client = MagicMock()
        client.get_entity = AsyncMock(return_value=MagicMock())
        client.send_message = AsyncMock()

        cfg = MagicMock(club_key="round_table")
        row = MagicMock(
            id=2,
            telegram_chat_id=-100456,
            invite_link="https://t.me/+fresh",
            telegram_chat_title="RT / 7108-8970 / Penner77",
        )
        player = MagicMock()
        player.id = 7876709437
        player.username = "Penner77"
        player.first_name = "Penner"
        player.last_name = "77"

        with (
            patch(
                "bot.services.mtproto_dm_gc_listener.ensure_player_in_support_group",
                new=AsyncMock(return_value="invited_ok"),
            ),
            patch(
                "bot.services.group_chat_invite_links.resolve_support_group_invite_link",
                new=AsyncMock(return_value=("https://t.me/+fresh", "stored_valid")),
            ),
            patch("bot.services.mtproto_dm_gc_listener.update_support_group_chat_row"),
        ):
            await _flow_existing_group(
                client,
                cfg,
                row,
                player,
                listener_label="@rt",
                trigger="outgoing_gc_command",
            )

        self.assertEqual(client.send_message.await_count, 1)
        sent = client.send_message.await_args.args[1]
        self.assertIn("https://t.me/+fresh", sent)
        self.assertNotIn("Player:", sent)


if __name__ == "__main__":
    unittest.main()
