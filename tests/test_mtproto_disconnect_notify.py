"""Tests for MTProto disconnect admin DM notifications."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.mtproto_club_health import STATUS_CONNECTED, STATUS_DISCONNECTED
from bot.services.mtproto_track_contact import (
    _mtproto_disconnect_last_notify,
    clear_mtproto_disconnect_notify_cooldown,
    format_mtproto_disconnect_slack_text,
    format_mtproto_disconnect_telegram_text,
    notify_club_gc_mtproto_disconnected,
    set_contact_save_notify_bot,
)

_SETTINGS = "https://gg-support-bot-2025-6f96168018cf.herokuapp.com/settings"


class TestFormatMtprotoDisconnectSlack(unittest.TestCase):
    def test_includes_qr_login_code_and_cloud_password(self) -> None:
        text = format_mtproto_disconnect_slack_text(
            "Round Table",
            settings_url=_SETTINGS,
        )
        self.assertEqual(
            text,
            "\n".join(
                [
                    "*Telegram session disconnected.*",
                    "Club: Round Table",
                    "",
                    (
                        "/add, /cash, and new groups from player DMs will not work "
                        "until this account is signed in again."
                    ),
                    "",
                    "1. Open GG Dashboard → Settings → Telegram login.",
                    "2. Select Round Table.",
                    "3. Sign in either way:",
                    (
                        "   • Login with QR code. On the club phone: Telegram → "
                        "Settings → Devices → Link Desktop Device, then scan the QR."
                    ),
                    (
                        "   • Send login code. Enter the code Telegram sends, then tap "
                        "Sign in with code."
                    ),
                    (
                        "4. If it asks for a cloud password, enter that on the "
                        "dashboard too."
                    ),
                    "",
                    f"<{_SETTINGS}|Open Settings>",
                ]
            ),
        )

    def test_omits_link_without_settings_url(self) -> None:
        text = format_mtproto_disconnect_slack_text("ClubGTO")
        self.assertNotIn("Open Settings", text)
        self.assertIn("Club: ClubGTO", text)
        self.assertIn("2. Select ClubGTO.", text)


class TestFormatMtprotoDisconnectTelegram(unittest.TestCase):
    def test_matches_slack_steps_with_plain_settings_url(self) -> None:
        text = format_mtproto_disconnect_telegram_text(
            "Round Table",
            settings_url=_SETTINGS,
        )
        self.assertEqual(
            text,
            "\n".join(
                [
                    "Telegram session disconnected.",
                    "Club: Round Table",
                    "",
                    (
                        "/add, /cash, and new groups from player DMs will not work "
                        "until this account is signed in again."
                    ),
                    "",
                    "1. Open GG Dashboard → Settings → Telegram login.",
                    "2. Select Round Table.",
                    "3. Sign in either way:",
                    (
                        "   • Login with QR code. On the club phone: Telegram → "
                        "Settings → Devices → Link Desktop Device, then scan the QR."
                    ),
                    (
                        "   • Send login code. Enter the code Telegram sends, then tap "
                        "Sign in with code."
                    ),
                    (
                        "4. If it asks for a cloud password, enter that on the "
                        "dashboard too."
                    ),
                    "",
                    _SETTINGS,
                ]
            ),
        )

    def test_omits_url_when_missing(self) -> None:
        text = format_mtproto_disconnect_telegram_text("ClubGTO")
        self.assertNotIn("http", text)
        self.assertIn("2. Select ClubGTO.", text)
        self.assertIn("Login with QR code", text)
        self.assertIn("Send login code", text)
        self.assertIn("cloud password", text)


class TestNotifyClubGcMtprotoDisconnected(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _mtproto_disconnect_last_notify.clear()
        set_contact_save_notify_bot(MagicMock(send_message=AsyncMock()))
        self.slack_patch = patch(
            "bot.services.slack_ops_notify.notify_slack_escalation",
            new_callable=AsyncMock,
        )
        self.mock_slack = self.slack_patch.start()
        self.url_patch = patch(
            "bot.services.mtproto_track_contact._mtproto_disconnect_settings_url",
            return_value=_SETTINGS,
        )
        self.url_patch.start()

    def tearDown(self) -> None:
        self.url_patch.stop()
        self.slack_patch.stop()
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
        self.assertEqual(
            kwargs["text"],
            format_mtproto_disconnect_telegram_text(
                "Round Table",
                settings_url=_SETTINGS,
            ),
        )
        self.assertNotIn("head admin or engineer", kwargs["text"])
        self.mock_slack.assert_awaited_once_with(
            format_mtproto_disconnect_slack_text(
                "Round Table",
                settings_url=_SETTINGS,
            ),
            source="mtproto_session_disconnected",
        )

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
        self.mock_slack.assert_awaited_once()

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
        self.assertEqual(self.mock_slack.await_count, 2)


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
