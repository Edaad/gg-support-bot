"""Tests for MTProto disconnect admin DM notifications."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.mtproto_club_health import STATUS_CONNECTED, STATUS_DISCONNECTED
from bot.services.mtproto_track_contact import (
    _mtproto_disconnect_last_notify,
    clear_mtproto_disconnect_notify_cooldown,
    notify_club_gc_mtproto_disconnected,
    set_contact_save_notify_bot,
)


class TestNotifyClubGcMtprotoDisconnected(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _mtproto_disconnect_last_notify.clear()
        set_contact_save_notify_bot(MagicMock(send_message=AsyncMock()))

    def tearDown(self) -> None:
        set_contact_save_notify_bot(None)
        _mtproto_disconnect_last_notify.clear()

    async def test_sends_admin_dm_with_escalation_copy(self) -> None:
        cfg = MagicMock(
            club_key="round_table",
            club_display_name="Round Table",
            command_admin_user_id=6713100304,
        )
        bot = MagicMock(send_message=AsyncMock())
        set_contact_save_notify_bot(bot)

        await notify_club_gc_mtproto_disconnected(
            cfg,
            status_detail="Telethon client disconnected on worker.",
        )

        bot.send_message.assert_awaited_once()
        kwargs = bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], 6713100304)
        text = kwargs["text"]
        self.assertIn("MTProto is not connected", text)
        self.assertIn("head admin or engineer", text)
        self.assertIn("/add", text)
        self.assertIn("/cash", text)
        self.assertIn("automatic /gc", text)
        self.assertIn("Telethon client disconnected on worker.", text)

    async def test_cooldown_skips_repeat_within_window(self) -> None:
        cfg = MagicMock(
            club_key="round_table",
            club_display_name="Round Table",
            command_admin_user_id=6713100304,
        )
        bot = MagicMock(send_message=AsyncMock())
        set_contact_save_notify_bot(bot)

        await notify_club_gc_mtproto_disconnected(cfg, status_detail="first")
        await notify_club_gc_mtproto_disconnected(cfg, status_detail="second")

        bot.send_message.assert_awaited_once()

    async def test_clear_cooldown_allows_repeat_after_reconnect(self) -> None:
        cfg = MagicMock(
            club_key="round_table",
            club_display_name="Round Table",
            command_admin_user_id=6713100304,
        )
        bot = MagicMock(send_message=AsyncMock())
        set_contact_save_notify_bot(bot)

        await notify_club_gc_mtproto_disconnected(cfg, status_detail="first")
        clear_mtproto_disconnect_notify_cooldown("round_table")
        await notify_club_gc_mtproto_disconnected(cfg, status_detail="second")

        self.assertEqual(bot.send_message.await_count, 2)


class TestReportClubHealthDisconnectNotify(unittest.IsolatedAsyncioTestCase):
    async def test_notifies_on_unhealthy_status(self) -> None:
        from bot.services.mtproto_dm_gc_listener import _report_club_health

        with (
            patch(
                "bot.services.mtproto_dm_gc_listener.persist_club_health",
            ) as mock_persist,
            patch(
                "bot.services.mtproto_dm_gc_listener.notify_club_gc_mtproto_disconnected",
                new_callable=AsyncMock,
            ) as mock_notify,
            patch(
                "bot.services.mtproto_dm_gc_listener.CLUB_GC_CONFIG",
                {"round_table": MagicMock(club_key="round_table")},
            ),
        ):
            await _report_club_health(
                "round_table",
                worker_connected=False,
                session_valid=False,
                status=STATUS_DISCONNECTED,
                status_detail="ping failed",
            )

        mock_persist.assert_called_once()
        mock_notify.assert_awaited_once_with(
            mock_notify.await_args.args[0],
            status_detail="ping failed",
        )

    async def test_skips_notify_on_teardown(self) -> None:
        from bot.services.mtproto_dm_gc_listener import _report_club_health

        with (
            patch("bot.services.mtproto_dm_gc_listener.persist_club_health"),
            patch(
                "bot.services.mtproto_dm_gc_listener.notify_club_gc_mtproto_disconnected",
                new_callable=AsyncMock,
            ) as mock_notify,
            patch(
                "bot.services.mtproto_dm_gc_listener.CLUB_GC_CONFIG",
                {"round_table": MagicMock(club_key="round_table")},
            ),
        ):
            await _report_club_health(
                "round_table",
                worker_connected=False,
                session_valid=False,
                status=STATUS_DISCONNECTED,
                status_detail="Listener cycle ended",
                notify_on_disconnect=False,
            )

        mock_notify.assert_not_awaited()

    async def test_clears_cooldown_on_connected(self) -> None:
        from bot.services.mtproto_dm_gc_listener import _report_club_health

        with (
            patch("bot.services.mtproto_dm_gc_listener.persist_club_health"),
            patch(
                "bot.services.mtproto_dm_gc_listener.clear_mtproto_disconnect_notify_cooldown",
            ) as mock_clear,
            patch(
                "bot.services.mtproto_dm_gc_listener.notify_club_gc_mtproto_disconnected",
                new_callable=AsyncMock,
            ) as mock_notify,
            patch(
                "bot.services.mtproto_dm_gc_listener.CLUB_GC_CONFIG",
                {"round_table": MagicMock(club_key="round_table")},
            ),
        ):
            await _report_club_health(
                "round_table",
                worker_connected=True,
                session_valid=True,
                status=STATUS_CONNECTED,
                status_detail=None,
            )

        mock_clear.assert_called_once_with("round_table")
        mock_notify.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
