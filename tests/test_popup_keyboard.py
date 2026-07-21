"""Unit tests for player popup reply keyboard helpers."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
        self.assertEqual(kwargs["when"], pk.POPUP_IDLE_SECONDS)
        self.assertEqual(kwargs["data"]["chat_id"], -100)
        self.assertEqual(kwargs["data"]["reply_to_message_id"], 9)
        self.assertEqual(kwargs["data"]["player_user_id"], 5)

    def test_schedule_skipped_when_not_eligible(self):
        context = MagicMock()
        with patch.object(pk, "popup_keyboard_eligible", return_value=False):
            pk.schedule_popup_keyboard_idle(context, chat_id=-100)
        context.job_queue.run_once.assert_not_called()


class ButtonLabelTests(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(pk.BTN_DEPOSIT, "Deposit")
        self.assertEqual(pk.BTN_CASHOUT, "Cashout")
        self.assertEqual(pk.BTN_OTHER, "Other")
        self.assertEqual(pk.BUTTON_LABELS, {"Deposit", "Cashout", "Other"})


if __name__ == "__main__":
    unittest.main()
