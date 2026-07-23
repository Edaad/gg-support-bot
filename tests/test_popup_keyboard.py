"""Unit tests for player popup reply keyboard helpers."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import popup_keyboard as pk


class PopupKeyboardEnabledTests(unittest.TestCase):
    def test_test_bot_worker_always_enabled(self):
        with patch.object(pk, "is_test_bot_worker", return_value=True):
            self.assertTrue(pk.popup_keyboard_enabled(None))
            self.assertTrue(pk.popup_keyboard_enabled(99))

    def test_main_bot_uses_club_flag(self):
        club_on = SimpleNamespace(enable_popup_keyboard=True)
        club_off = SimpleNamespace(enable_popup_keyboard=False)

        with patch.object(pk, "is_test_bot_worker", return_value=False):
            with patch.object(pk, "get_db") as get_db:
                session = MagicMock()
                get_db.return_value.__enter__.return_value = session
                session.get.return_value = club_on
                self.assertTrue(pk.popup_keyboard_enabled(2))

                session.get.return_value = club_off
                self.assertFalse(pk.popup_keyboard_enabled(2))

                session.get.return_value = None
                self.assertFalse(pk.popup_keyboard_enabled(2))


class SupportSenderTests(unittest.TestCase):
    def test_admin_is_support(self):
        user = SimpleNamespace(id=111, is_bot=False, username="anyone")
        with patch.object(pk, "ADMIN_USER_IDS", {111}):
            with patch.object(pk, "is_club_staff", return_value=False):
                with patch.object(pk, "get_club_gc_config_by_link_club_id", return_value=None):
                    self.assertTrue(pk.is_support_sender(user, club_id=2))

    def test_club_staff_is_support(self):
        user = SimpleNamespace(id=222, is_bot=False, username="am")
        with patch.object(pk, "ADMIN_USER_IDS", set()):
            with patch.object(pk, "is_club_staff", return_value=True):
                self.assertTrue(pk.is_support_sender(user, club_id=2))

    def test_gc_invite_username_is_support(self):
        user = SimpleNamespace(id=333, is_bot=False, username="RoundTableSupport2")
        cfg = SimpleNamespace(
            command_admin_user_id=0,
            bot_account=None,
        )
        with patch.object(pk, "ADMIN_USER_IDS", set()):
            with patch.object(pk, "is_club_staff", return_value=False):
                with patch.object(pk, "get_club_gc_config_by_link_club_id", return_value=cfg):
                    with patch.object(
                        pk, "get_gc_users_to_add", return_value=("@RoundTableSupport2",)
                    ):
                        self.assertTrue(pk.is_support_sender(user, club_id=2))

    def test_player_is_not_support(self):
        user = SimpleNamespace(id=444, is_bot=False, username="playerone")
        with patch.object(pk, "ADMIN_USER_IDS", set()):
            with patch.object(pk, "is_club_staff", return_value=False):
                with patch.object(pk, "get_club_gc_config_by_link_club_id", return_value=None):
                    self.assertFalse(pk.is_support_sender(user, club_id=2))

    def test_bot_is_support(self):
        user = SimpleNamespace(id=1, is_bot=True, username="bot")
        self.assertTrue(pk.is_support_sender(user, club_id=2))


class IdleJobNameTests(unittest.TestCase):
    def test_idle_job_name_unique_per_chat(self):
        self.assertEqual(pk.idle_job_name(123), "popup_keyboard_idle_123")
        self.assertEqual(pk.idle_job_name(-1001), "popup_keyboard_idle_-1001")
        self.assertNotEqual(pk.idle_job_name(1), pk.idle_job_name(2))


class ScheduleIdleTests(unittest.TestCase):
    def test_schedule_cancels_and_reschedules(self):
        old_job = MagicMock()
        context = MagicMock()
        context.job_queue.get_jobs_by_name.return_value = [old_job]
        context.chat_data = {"popup_kb_last_player_message_id": 9, "popup_kb_last_player_user_id": 5}

        with patch.object(pk, "popup_keyboard_eligible", return_value=True):
            pk.schedule_popup_keyboard_idle(context, chat_id=-100)

        old_job.schedule_removal.assert_called_once()
        context.job_queue.run_once.assert_called_once()
        kwargs = context.job_queue.run_once.call_args.kwargs
        self.assertEqual(kwargs["name"], "popup_keyboard_idle_-100")
        self.assertEqual(kwargs["when"], pk.popup_idle_seconds())
        self.assertEqual(kwargs["data"]["chat_id"], -100)
        self.assertEqual(kwargs["data"]["reply_to_message_id"], 9)
        self.assertEqual(kwargs["data"]["player_user_id"], 5)

    def test_schedule_uses_30s_on_test_bot(self):
        context = MagicMock()
        context.job_queue.get_jobs_by_name.return_value = []
        context.chat_data = {}

        with patch.object(pk, "popup_keyboard_eligible", return_value=True):
            with patch.object(pk, "is_test_bot_worker", return_value=True):
                with patch.object(pk, "fetch_player_telegram_user_id_for_chat", return_value=5):
                    pk.schedule_popup_keyboard_idle(context, chat_id=-100)

        kwargs = context.job_queue.run_once.call_args.kwargs
        self.assertEqual(kwargs["when"], 30)

    def test_schedule_uses_5min_on_main_bot(self):
        context = MagicMock()
        context.job_queue.get_jobs_by_name.return_value = []
        context.chat_data = {}

        with patch.object(pk, "popup_keyboard_eligible", return_value=True):
            with patch.object(pk, "is_test_bot_worker", return_value=False):
                with patch.object(pk, "fetch_player_telegram_user_id_for_chat", return_value=5):
                    pk.schedule_popup_keyboard_idle(context, chat_id=-100)

        kwargs = context.job_queue.run_once.call_args.kwargs
        self.assertEqual(kwargs["when"], 300)

    def test_schedule_skipped_when_not_eligible(self):
        context = MagicMock()
        with patch.object(pk, "popup_keyboard_eligible", return_value=False):
            pk.schedule_popup_keyboard_idle(context, chat_id=-100)
        context.job_queue.run_once.assert_not_called()


class ButtonLabelTests(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(pk.BTN_DEPOSIT, "/deposit")
        self.assertEqual(pk.BTN_CASHOUT, "/cashout")
        self.assertEqual(pk.BUTTON_LABELS, {"/deposit", "/cashout"})
        self.assertNotIn("Other", pk.BUTTON_LABELS)
        self.assertFalse(hasattr(pk, "BTN_OTHER"))
        self.assertIn("request was handled", pk.INSTALL_COPY)
        self.assertEqual(pk.STRIP_COPY, "We'll be with you in just a second.")


class FlowCommandTextTests(unittest.TestCase):
    def test_deposit_cashout_withdraw(self):
        self.assertTrue(pk.is_flow_command_text("/deposit"))
        self.assertTrue(pk.is_flow_command_text("/cashout"))
        self.assertTrue(pk.is_flow_command_text("/withdraw"))
        self.assertTrue(pk.is_flow_command_text("/deposit@MyBot"))
        self.assertFalse(pk.is_flow_command_text("hello"))
        self.assertFalse(pk.is_flow_command_text(None))


class SelectiveInstallPayloadTests(unittest.TestCase):
    def test_install_and_strip_copy(self):
        self.assertIn("request was handled", pk.INSTALL_COPY)
        self.assertEqual(pk.STRIP_COPY, "We'll be with you in just a second.")
        self.assertNotIn("@", pk.INSTALL_COPY)
        self.assertNotIn("@", pk.STRIP_COPY)


class InstalledFlagTests(unittest.TestCase):
    def setUp(self):
        pk.clear_installed_memory_for_tests()

    def tearDown(self):
        pk.clear_installed_memory_for_tests()

    def test_get_false_without_row(self):
        with patch.object(pk, "is_test_bot_worker", return_value=False):
            with patch.object(
                pk, "fetch_support_group_chat_by_telegram_chat_id", return_value=None
            ):
                self.assertFalse(pk.get_popup_keyboard_installed(-1))

    def test_get_reads_row(self):
        row = SimpleNamespace(popup_keyboard_installed=True)
        with patch.object(pk, "is_test_bot_worker", return_value=False):
            with patch.object(
                pk, "fetch_support_group_chat_by_telegram_chat_id", return_value=row
            ):
                self.assertTrue(pk.get_popup_keyboard_installed(-1))

    def test_set_fails_without_row(self):
        with patch.object(pk, "is_test_bot_worker", return_value=False):
            with patch.object(
                pk, "fetch_support_group_chat_by_telegram_chat_id", return_value=None
            ):
                self.assertFalse(pk.set_popup_keyboard_installed(-1, True))

    def test_set_updates_row(self):
        row = SimpleNamespace(id=7, popup_keyboard_installed=False)
        with patch.object(pk, "is_test_bot_worker", return_value=False):
            with patch.object(
                pk, "fetch_support_group_chat_by_telegram_chat_id", return_value=row
            ):
                with patch.object(
                    pk, "update_support_group_chat_row", return_value=(True, None)
                ) as upd:
                    self.assertTrue(pk.set_popup_keyboard_installed(-1, True))
                    upd.assert_called_once_with(7, popup_keyboard_installed=True)

    def test_test_bot_uses_memory_flag(self):
        with patch.object(pk, "is_test_bot_worker", return_value=True):
            with patch.object(pk, "update_support_group_chat_row") as upd:
                self.assertFalse(pk.get_popup_keyboard_installed(-5234716365))
                self.assertTrue(pk.set_popup_keyboard_installed(-5234716365, True))
                self.assertTrue(pk.get_popup_keyboard_installed(-5234716365))
                self.assertTrue(pk.set_popup_keyboard_installed(-5234716365, False))
                self.assertFalse(pk.get_popup_keyboard_installed(-5234716365))
                upd.assert_not_called()


class UpsertIntegrityTests(unittest.TestCase):
    def test_test_bot_skips_db_upsert(self):
        with patch.object(pk, "is_test_bot_worker", return_value=True):
            with patch.object(pk, "update_support_group_chat_row") as upd:
                with patch.object(pk, "fetch_support_group_chat_by_telegram_chat_id") as fetch:
                    self.assertTrue(
                        pk.upsert_player_telegram_user_id(
                            -5234716365, 5821458817, username="jz034"
                        )
                    )
                    fetch.assert_not_called()
                    upd.assert_not_called()


class InstallSkipTests(unittest.IsolatedAsyncioTestCase):
    async def test_install_skips_without_support_group_row(self):
        bot = AsyncMock()
        with patch.object(pk, "popup_keyboard_eligible", return_value=True):
            with patch.object(pk, "is_test_bot_worker", return_value=False):
                with patch.object(
                    pk, "fetch_support_group_chat_by_telegram_chat_id", return_value=None
                ):
                    ok = await pk.install_popup_keyboard(bot, chat_id=-100)
        self.assertFalse(ok)
        bot.send_message.assert_not_called()

    async def test_test_bot_install_without_row_when_player_known(self):
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
        bot.delete_message = AsyncMock()
        ctx = MagicMock()
        ctx.chat_data = {
            "popup_kb_last_player_user_id": 42,
            "popup_kb_last_player_message_id": 7,
            "popup_kb_last_player_username": "me",
        }
        pk.clear_installed_memory_for_tests()
        with patch.object(pk, "popup_keyboard_eligible", return_value=True):
            with patch.object(pk, "is_test_bot_worker", return_value=True):
                with patch.object(
                    pk, "fetch_support_group_chat_by_telegram_chat_id", return_value=None
                ):
                    with patch.object(pk, "get_club_for_chat", return_value=None):
                        ok = await pk.install_popup_keyboard(
                            bot, chat_id=-100, context=ctx
                        )
                        self.assertTrue(ok)
                        self.assertTrue(pk.get_popup_keyboard_installed(-100))
        pk.clear_installed_memory_for_tests()


    async def test_silent_strip_noop_when_not_installed(self):
        bot = AsyncMock()
        with patch.object(pk, "get_popup_keyboard_installed", return_value=False):
            ok = await pk.silent_strip_if_installed(bot, chat_id=-100)
        self.assertFalse(ok)
        bot.send_message.assert_not_called()


class SendSilentMarkupTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_does_not_delete(self):
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=99))
        bot.delete_message = AsyncMock()
        ok = await pk._send_silent_markup(
            bot,
            chat_id=-100,
            text=pk.INSTALL_COPY,
            reply_markup=pk.keyboard_markup(),
        )
        self.assertTrue(ok)
        bot.send_message.assert_called_once()
        bot.delete_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
