"""Tests for ChannelsTooMuchError admin DM notifications."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.mtproto_track_contact import (
    _channels_too_much_last_notify,
    notify_club_gc_channels_too_much,
    set_contact_save_notify_bot,
)


class TestNotifyClubGcChannelsTooMuch(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _channels_too_much_last_notify.clear()
        set_contact_save_notify_bot(MagicMock(send_message=AsyncMock()))

    def tearDown(self) -> None:
        set_contact_save_notify_bot(None)
        _channels_too_much_last_notify.clear()

    async def test_sends_admin_dm_with_player_and_trigger(self) -> None:
        cfg = MagicMock(
            club_key="round_table",
            club_display_name="Round Table",
            command_admin_user_id=6713100304,
        )
        bot = MagicMock(send_message=AsyncMock())
        set_contact_save_notify_bot(bot)

        await notify_club_gc_channels_too_much(
            cfg,
            player_label="Hammy 702 [id=6973705568]",
            trigger="incoming_dm",
        )

        bot.send_message.assert_awaited_once()
        kwargs = bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], 6713100304)
        self.assertIn("ChannelsTooMuchError", kwargs["text"])
        self.assertIn("Hammy 702", kwargs["text"])
        self.assertIn("incoming_dm", kwargs["text"])

    async def test_cooldown_skips_repeat_within_window(self) -> None:
        cfg = MagicMock(
            club_key="round_table",
            club_display_name="Round Table",
            command_admin_user_id=6713100304,
        )
        bot = MagicMock(send_message=AsyncMock())
        set_contact_save_notify_bot(bot)

        await notify_club_gc_channels_too_much(
            cfg, player_label="Player A [id=1]", trigger="incoming_dm"
        )
        await notify_club_gc_channels_too_much(
            cfg, player_label="Player B [id=2]", trigger="incoming_dm"
        )

        bot.send_message.assert_awaited_once()


class TestFlowNewGroupChannelsTooMuch(unittest.IsolatedAsyncioTestCase):
    async def test_notifies_admin_on_channels_too_much(self) -> None:
        from bot.services.mtproto_dm_gc_listener import _flow_new_group

        cfg = MagicMock(club_key="round_table", club_display_name="Round Table")
        player = MagicMock()
        player.id = 6973705568
        player.username = None
        player.first_name = "Hammy"
        player.last_name = "702"

        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=6713100304))

        class ChannelsTooMuchError(Exception):
            pass

        with (
            patch(
                "bot.services.mtproto_dm_gc_listener.create_support_group",
                new_callable=AsyncMock,
                side_effect=ChannelsTooMuchError("too many"),
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener.notify_club_gc_channels_too_much",
                new_callable=AsyncMock,
            ) as mock_notify,
        ):
            await _flow_new_group(
                client,
                cfg,
                player,
                None,
                None,
                listener_label="@rt [id=1]",
                trigger="incoming_dm",
            )

        mock_notify.assert_awaited_once_with(
            cfg,
            player_label="Hammy 702 [id=6973705568]",
            trigger="incoming_dm",
        )


if __name__ == "__main__":
    unittest.main()
