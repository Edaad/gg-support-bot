"""Unit tests for shared group activity detection and escalation notification."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import group_activity as ga
from bot.services import escalation_notification as esc


class PaymentConfirmRegexTests(unittest.TestCase):
    def test_matches_sent_phrases(self):
        self.assertTrue(ga.is_payment_confirm_text("sent"))
        self.assertTrue(ga.is_payment_confirm_text("I just sent it"))
        self.assertTrue(ga.is_payment_confirm_text("paid $100"))
        self.assertTrue(ga.is_payment_confirm_text("made the payment"))
        self.assertTrue(ga.is_payment_confirm_text("payment sent already"))
        self.assertTrue(ga.is_payment_confirm_text("all done"))
        self.assertTrue(ga.is_payment_confirm_text("done"))
        self.assertTrue(ga.is_payment_confirm_text("hey I sent the money thanks"))

    def test_rejects_unrelated(self):
        self.assertFalse(ga.is_payment_confirm_text("hello"))
        self.assertFalse(ga.is_payment_confirm_text("how much is the min deposit"))
        self.assertFalse(ga.is_payment_confirm_text(""))
        self.assertFalse(ga.is_payment_confirm_text(None))


class MediaDetectionTests(unittest.TestCase):
    def test_photo_counts(self):
        msg = SimpleNamespace(
            photo=[object()],
            video=None,
            document=None,
            animation=None,
            voice=None,
            video_note=None,
            audio=None,
            sticker=None,
        )
        self.assertTrue(ga.message_has_media(msg))

    def test_text_only_false(self):
        msg = SimpleNamespace(
            photo=None,
            video=None,
            document=None,
            animation=None,
            voice=None,
            video_note=None,
            audio=None,
            sticker=None,
        )
        self.assertFalse(ga.message_has_media(msg))


class SilenceDetectionTests(unittest.TestCase):
    def setUp(self):
        ga.clear_activity_state_for_tests()

    def test_cold_start_no_fire(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        obs = ga.record_human_message(1, role="player", now=t0, silence_seconds=600)
        self.assertFalse(obs.should_fire_idle)

    def test_player_after_silence_fires_once(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        ga.record_human_message(1, role="player", now=t0, silence_seconds=600)
        t1 = t0 + timedelta(seconds=601)
        obs = ga.record_human_message(1, role="player", now=t1, silence_seconds=600)
        self.assertTrue(obs.should_fire_idle)
        t2 = t1 + timedelta(seconds=30)
        obs2 = ga.record_human_message(1, role="player", now=t2, silence_seconds=600)
        self.assertFalse(obs2.should_fire_idle)

    def test_staff_then_player_without_silence_no_fire(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        ga.record_human_message(1, role="staff", now=t0, silence_seconds=600)
        t1 = t0 + timedelta(seconds=30)
        obs = ga.record_human_message(1, role="player", now=t1, silence_seconds=600)
        self.assertFalse(obs.should_fire_idle)

    def test_staff_then_silence_then_player_fires(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        ga.record_human_message(1, role="staff", now=t0, silence_seconds=600)
        t1 = t0 + timedelta(seconds=601)
        obs = ga.record_human_message(1, role="player", now=t1, silence_seconds=600)
        self.assertTrue(obs.should_fire_idle)

    def test_episode_resets_after_silence(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        ga.record_human_message(1, role="player", now=t0, silence_seconds=600)
        t1 = t0 + timedelta(seconds=601)
        self.assertTrue(
            ga.record_human_message(
                1, role="player", now=t1, silence_seconds=600
            ).should_fire_idle
        )
        t2 = t1 + timedelta(seconds=601)
        self.assertTrue(
            ga.record_human_message(
                1, role="player", now=t2, silence_seconds=600
            ).should_fire_idle
        )


class SupportSenderTests(unittest.TestCase):
    def test_admin_is_support(self):
        user = SimpleNamespace(id=111, is_bot=False, username="anyone")
        with patch.object(ga, "ADMIN_USER_IDS", {111}):
            with patch.object(ga, "is_club_staff", return_value=False):
                with patch.object(
                    ga, "get_club_gc_config_by_link_club_id", return_value=None
                ):
                    self.assertTrue(ga.is_support_sender(user, club_id=2))

    def test_player_is_not_support(self):
        user = SimpleNamespace(id=444, is_bot=False, username="playerone")
        with patch.object(ga, "ADMIN_USER_IDS", set()):
            with patch.object(ga, "is_club_staff", return_value=False):
                with patch.object(
                    ga, "get_club_gc_config_by_link_club_id", return_value=None
                ):
                    self.assertFalse(ga.is_support_sender(user, club_id=2))


class EscalationCopyTests(unittest.TestCase):
    def test_player_idle_copy(self):
        with patch.object(esc, "_club_display_name", return_value="Round Table"):
            with patch.object(esc, "get_group_name", return_value="RT / 12345"):
                text = esc.format_escalation_slack_text(
                    esc.REASON_PLAYER_IDLE,
                    club_id=1,
                    chat_id=-100,
                    title="RT / 12345",
                )
        self.assertIn("A player just reached out.", text)
        self.assertIn("Club: Round Table", text)
        self.assertIn("Group: RT / 12345 (-100)", text)
        self.assertNotIn("user_id", text.lower())

    def test_cashout_copy(self):
        with patch.object(esc, "_club_display_name", return_value="Aces"):
            text = esc.format_escalation_slack_text(
                esc.REASON_CASHOUT_STARTED,
                club_id=2,
                chat_id=-200,
                title="AT / 9",
            )
        self.assertIn("Cash out initiated.", text)


class DepositSentChaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ga.clear_activity_state_for_tests()

    async def test_confirm_arms_then_followup_escalates(self):
        ga.mark_deposit_instructions_pending(5)
        context = MagicMock()
        jq = MagicMock()
        context.job_queue = jq

        with patch.object(esc, "schedule_deposit_sent_watch") as schedule:
            consumed = await esc.handle_deposit_sent_player_signal(
                context,
                5,
                club_id=1,
                title="G",
                is_confirm_signal=True,
            )
            self.assertTrue(consumed)
            schedule.assert_called_once()

        ga.mark_deposit_sent_watch_armed(5)
        with patch.object(
            esc, "notify_escalation_slack", new_callable=AsyncMock
        ) as notify:
            with patch.object(esc, "cancel_deposit_sent_watch") as cancel:
                consumed2 = await esc.handle_deposit_sent_player_signal(
                    context,
                    5,
                    club_id=1,
                    title="G",
                    is_confirm_signal=False,
                )
                self.assertTrue(consumed2)
                cancel.assert_called_once()
                notify.assert_awaited_once()
                self.assertEqual(
                    notify.await_args.args[0], esc.REASON_DEPOSIT_SENT_FOLLOWUP
                )


if __name__ == "__main__":
    unittest.main()
