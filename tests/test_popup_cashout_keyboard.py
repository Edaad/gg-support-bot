"""Tests for deposit-only popup keyboard when cashout is blocked."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import club as club_svc
from bot.services import popup_keyboard as pk


class CashoutShownOnPopupKeyboardTests(unittest.TestCase):
    def test_both_rules_off_shows_cashout(self):
        with patch.object(club_svc, "has_permanent_cashout_bypass", return_value=False):
            with patch.object(
                club_svc,
                "get_cooldown_settings",
                return_value={
                    "cooldown_enabled": False,
                    "cooldown_hours": 24,
                    "hours_enabled": False,
                    "hours_start": "08:00",
                    "hours_end": "23:00",
                },
            ):
                self.assertTrue(club_svc.cashout_shown_on_popup_keyboard(2, -100))

    def test_in_cooldown_hides_cashout(self):
        now = datetime.now(timezone.utc)
        last = now - timedelta(hours=1)
        with patch.object(club_svc, "has_permanent_cashout_bypass", return_value=False):
            with patch.object(
                club_svc,
                "get_cooldown_settings",
                return_value={
                    "cooldown_enabled": True,
                    "cooldown_hours": 24,
                    "hours_enabled": False,
                    "hours_start": "08:00",
                    "hours_end": "23:00",
                },
            ):
                with patch.object(club_svc, "get_last_activity", return_value=last):
                    self.assertFalse(
                        club_svc.cashout_shown_on_popup_keyboard(2, -100)
                    )

    def test_outside_hours_hides_cashout(self):
        with patch.object(club_svc, "has_permanent_cashout_bypass", return_value=False):
            with patch.object(
                club_svc,
                "get_cooldown_settings",
                return_value={
                    "cooldown_enabled": False,
                    "cooldown_hours": 24,
                    "hours_enabled": True,
                    "hours_start": "08:00",
                    "hours_end": "23:00",
                },
            ):
                with patch.object(club_svc, "_is_within_hours", return_value=False):
                    self.assertFalse(
                        club_svc.cashout_shown_on_popup_keyboard(2, -100)
                    )

    def test_permanent_bypass_shows_cashout_despite_cooldown(self):
        now = datetime.now(timezone.utc)
        last = now - timedelta(hours=1)
        with patch.object(club_svc, "has_permanent_cashout_bypass", return_value=True):
            with patch.object(
                club_svc,
                "get_cooldown_settings",
                return_value={
                    "cooldown_enabled": True,
                    "cooldown_hours": 24,
                    "hours_enabled": True,
                    "hours_start": "08:00",
                    "hours_end": "23:00",
                },
            ):
                with patch.object(club_svc, "get_last_activity", return_value=last):
                    self.assertTrue(club_svc.cashout_shown_on_popup_keyboard(2, -100))

    def test_one_time_bypass_ignored_while_in_cooldown(self):
        """Unused one-time bypass must not affect keyboard (no consume either)."""
        now = datetime.now(timezone.utc)
        last = now - timedelta(hours=1)
        with patch.object(club_svc, "has_permanent_cashout_bypass", return_value=False):
            with patch.object(
                club_svc,
                "get_cooldown_settings",
                return_value={
                    "cooldown_enabled": True,
                    "cooldown_hours": 24,
                    "hours_enabled": False,
                    "hours_start": "08:00",
                    "hours_end": "23:00",
                },
            ):
                with patch.object(club_svc, "get_last_activity", return_value=last):
                    with patch.object(
                        club_svc, "check_and_consume_bypass"
                    ) as consume:
                        self.assertFalse(
                            club_svc.cashout_shown_on_popup_keyboard(2, -100)
                        )
                        consume.assert_not_called()

    def test_errors_fail_open(self):
        with patch.object(
            club_svc,
            "has_permanent_cashout_bypass",
            side_effect=RuntimeError("db down"),
        ):
            self.assertTrue(club_svc.cashout_shown_on_popup_keyboard(2, -100))


class KeyboardMarkupCashoutTests(unittest.TestCase):
    def test_full_keyboard_default(self):
        markup = pk.keyboard_markup()
        texts = [btn.text for row in markup.keyboard for btn in row]
        self.assertEqual(texts, [pk.BTN_DEPOSIT, pk.BTN_CASHOUT])

    def test_deposit_only_keyboard(self):
        markup = pk.keyboard_markup(include_cashout=False)
        texts = [btn.text for row in markup.keyboard for btn in row]
        self.assertEqual(texts, [pk.BTN_DEPOSIT])


class InstallDepositOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def test_install_uses_deposit_only_markup_when_cashout_hidden(self):
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
        ctx = MagicMock()
        ctx.chat_data = {
            "popup_kb_last_player_user_id": 42,
            "popup_kb_last_player_message_id": 7,
        }
        pk.clear_installed_memory_for_tests()
        with patch.object(pk, "popup_keyboard_eligible", return_value=True):
            with patch.object(pk, "is_test_bot_worker", return_value=True):
                with patch.object(
                    pk, "fetch_support_group_chat_by_telegram_chat_id", return_value=None
                ):
                    with patch.object(pk, "get_club_for_chat", return_value=3):
                        with patch.object(
                            pk, "cashout_shown_on_popup_keyboard", return_value=False
                        ):
                            ok = await pk.install_popup_keyboard(
                                bot, chat_id=-100, context=ctx
                            )
        self.assertTrue(ok)
        kwargs = bot.send_message.call_args.kwargs
        markup = kwargs["reply_markup"]
        texts = [btn.text for row in markup.keyboard for btn in row]
        self.assertEqual(texts, [pk.BTN_DEPOSIT])
        pk.clear_installed_memory_for_tests()


if __name__ == "__main__":
    unittest.main()
