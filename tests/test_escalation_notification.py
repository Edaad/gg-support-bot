"""Unit tests for shared group activity detection and escalation notification."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import group_activity as ga
from bot.services import escalation_notification as esc


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

    def test_null_last_human_player_fires(self):
        """Virgin / post-migrate state: first player message may escalate."""
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                obs = ga.record_human_message(
                    1, role="player", now=t0, silence_seconds=600
                )
        self.assertTrue(obs.should_fire_idle)
        self.assertTrue(obs.silence_elapsed)

    def test_null_last_human_staff_does_not_fire(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                obs = ga.record_human_message(
                    1, role="staff", now=t0, silence_seconds=600
                )
        self.assertFalse(obs.should_fire_idle)

    def test_player_after_silence_fires_once(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.record_human_message(1, role="player", now=t0, silence_seconds=600)
                t1 = t0 + timedelta(seconds=601)
                obs = ga.record_human_message(
                    1, role="player", now=t1, silence_seconds=600
                )
                self.assertTrue(obs.should_fire_idle)
                t2 = t1 + timedelta(seconds=30)
                obs2 = ga.record_human_message(
                    1, role="player", now=t2, silence_seconds=600
                )
                self.assertFalse(obs2.should_fire_idle)

    def test_staff_then_player_without_silence_no_fire(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.record_human_message(1, role="staff", now=t0, silence_seconds=600)
                t1 = t0 + timedelta(seconds=30)
                obs = ga.record_human_message(
                    1, role="player", now=t1, silence_seconds=600
                )
                self.assertFalse(obs.should_fire_idle)

    def test_staff_then_silence_then_player_fires(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.record_human_message(1, role="staff", now=t0, silence_seconds=600)
                t1 = t0 + timedelta(seconds=601)
                obs = ga.record_human_message(
                    1, role="player", now=t1, silence_seconds=600
                )
                self.assertTrue(obs.should_fire_idle)

    def test_episode_resets_after_silence(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
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

    def test_reset_idle_episode_clears_flag(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.record_human_message(1, role="player", now=t0, silence_seconds=600)
                t1 = t0 + timedelta(seconds=601)
                self.assertTrue(
                    ga.record_human_message(
                        1, role="player", now=t1, silence_seconds=600
                    ).should_fire_idle
                )
                self.assertTrue(ga.get_chat_activity_state(1).idle_episode_fired)
                ga.reset_idle_episode(1)
                self.assertFalse(ga.get_chat_activity_state(1).idle_episode_fired)


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
                    message_text="Need help with my deposit",
                )
        self.assertIn("*A player just reached out.*", text)
        self.assertIn("Club: Round Table", text)
        self.assertIn("`RT / 12345`", text)
        self.assertIn("Need help with my deposit", text)
        self.assertNotIn("Group:", text)
        self.assertNotIn("-100", text)
        self.assertNotIn("user_id", text.lower())

    def test_player_idle_truncates_long_message(self):
        long = "x" * 600
        with patch.object(esc, "_club_display_name", return_value="Round Table"):
            text = esc.format_escalation_slack_text(
                esc.REASON_PLAYER_IDLE,
                club_id=1,
                chat_id=-100,
                title="RT / 1",
                message_text=long,
            )
        self.assertIn("…", text)
        self.assertLessEqual(len(text.split("\n")[-1]), esc.SLACK_MESSAGE_BODY_MAX_CHARS)

    def test_cashout_copy(self):
        with patch.object(esc, "_club_display_name", return_value="Aces"):
            text = esc.format_escalation_slack_text(
                esc.REASON_CASHOUT_STARTED,
                club_id=2,
                chat_id=-200,
                title="AT / 9",
                message_text="should not appear",
            )
        self.assertIn("*Cash out initiated.*", text)
        self.assertIn("`AT / 9`", text)
        self.assertNotIn("should not appear", text)

    def test_unbound_manual_deposit_headline(self):
        with patch.object(esc, "_club_display_name", return_value="Creator Club"):
            text = esc.format_escalation_slack_text(
                esc.REASON_DEPOSIT_SENT_UNBOUND,
                club_id=3,
                chat_id=-300,
                title="CC / 9255-5089 / Justin",
            )
        self.assertIn("*Manual deposit request.*", text)
        self.assertIn("`CC / 9255-5089 / Justin`", text)

    def test_new_player_onboarded_copy(self):
        with patch.object(esc, "_club_display_name", return_value="ClubGTO"):
            text = esc.format_escalation_slack_text(
                esc.REASON_NEW_PLAYER_ONBOARDED,
                club_id=4,
                chat_id=-100,
                title="GTO / 4661-4582 / Btwn",
            )
        self.assertIn(
            "*Welcome the new player who just joined the group chat.*", text
        )
        self.assertIn("Club: ClubGTO", text)
        self.assertIn("`GTO / 4661-4582 / Btwn`", text)

    def test_player_dm_reached_out_copy(self):
        with patch.object(esc, "_club_display_name", return_value="Round Table"):
            text = esc.format_escalation_slack_text(
                esc.REASON_PLAYER_DM_REACHED_OUT,
                club_id=2,
                chat_id=0,
                title="Btwn (@btwn)",
            )
        self.assertIn("*A player reached out in DM.*", text)
        self.assertIn("`Btwn (@btwn)`", text)

    def test_deposit_followup_copy_includes_message(self):
        with patch.object(esc, "_club_display_name", return_value="Creator Club"):
            text = esc.format_escalation_slack_text(
                esc.REASON_DEPOSIT_SENT_FOLLOWUP,
                club_id=3,
                chat_id=-300,
                title="CC / 1 / x",
                message_text="still waiting",
            )
        self.assertIn(
            "*Player sent a message after confirming they sent the payment.*",
            text,
        )
        self.assertIn("still waiting", text)

    def test_earlyrb_and_rpa_headlines(self):
        with patch.object(esc, "_club_display_name", return_value="Creator Club"):
            early = esc.format_escalation_slack_text(
                esc.REASON_EARLYRB_REQUESTED,
                club_id=3,
                chat_id=-1,
                title="CC / 1",
            )
            dep = esc.format_escalation_slack_text(
                esc.REASON_RPA_DEPOSIT_FAILED,
                club_id=3,
                chat_id=-1,
                title="CC / 1",
            )
            cash = esc.format_escalation_slack_text(
                esc.REASON_RPA_CASHOUT_FAILED,
                club_id=3,
                chat_id=-1,
                title="CC / 1",
            )
        self.assertIn("*Early rakeback requested.*", early)
        self.assertIn("*RPA deposit failed — add chips manually.*", dep)
        self.assertIn("*RPA cashout failed — claim chips manually.*", cash)

    def test_extract_player_message_prefers_text(self):
        msg = SimpleNamespace(text=" hello ", caption="cap", photo=None)
        self.assertEqual(esc.extract_player_message_for_slack(msg), "hello")

    def test_extract_player_message_media_placeholder(self):
        msg = SimpleNamespace(text=None, caption=None)
        self.assertEqual(
            esc.extract_player_message_for_slack(msg), esc.MEDIA_ONLY_PLACEHOLDER
        )


class PlayerContactLabelTests(unittest.TestCase):
    def test_name_and_username(self):
        self.assertEqual(
            esc.format_player_contact_label(
                display_name="Btwn", username="btwn"
            ),
            "Btwn (@btwn)",
        )

    def test_name_only(self):
        self.assertEqual(
            esc.format_player_contact_label(display_name="Btwn", username=None),
            "Btwn",
        )

    def test_username_only(self):
        self.assertEqual(
            esc.format_player_contact_label(display_name="", username="@btwn"),
            "@btwn",
        )


class DepositFollowupIgnoreTests(unittest.TestCase):
    def _text_msg(self, text: str):
        return SimpleNamespace(
            text=text,
            caption=None,
            photo=None,
            video=None,
            document=None,
            animation=None,
            voice=None,
            video_note=None,
            audio=None,
            sticker=None,
        )

    def test_ignore_sent_and_done(self):
        self.assertTrue(
            esc.should_ignore_deposit_sent_followup(self._text_msg("Sent!"))
        )
        self.assertTrue(
            esc.should_ignore_deposit_sent_followup(self._text_msg("done"))
        )
        self.assertTrue(
            esc.should_ignore_deposit_sent_followup(
                self._text_msg("I already sent it")
            )
        )

    def test_flag_other_text(self):
        self.assertFalse(
            esc.should_ignore_deposit_sent_followup(
                self._text_msg("where are my chips?")
            )
        )

    def test_ignore_media(self):
        msg = SimpleNamespace(
            text=None,
            caption="proof",
            photo=[object()],
            video=None,
            document=None,
            animation=None,
            voice=None,
            video_note=None,
            audio=None,
            sticker=None,
        )
        self.assertTrue(esc.should_ignore_deposit_sent_followup(msg))


class DepositSentChaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ga.clear_activity_state_for_tests()

    def _plain_text(self, text: str):
        return SimpleNamespace(
            text=text,
            caption=None,
            photo=None,
            video=None,
            document=None,
            animation=None,
            voice=None,
            video_note=None,
            audio=None,
            sticker=None,
        )

    async def test_followup_escalates_when_armed(self):
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.mark_deposit_sent_watch_armed(5)
                context = MagicMock()
                with patch.object(
                    esc, "notify_escalation_slack", new_callable=AsyncMock
                ) as notify:
                    with patch.object(esc, "cancel_deposit_sent_watch") as cancel:
                        consumed = await esc.handle_deposit_sent_player_followup(
                            context,
                            5,
                            club_id=1,
                            title="G",
                            message_text="where are my chips?",
                            message=self._plain_text("where are my chips?"),
                        )
                        self.assertTrue(consumed)
                        cancel.assert_called_once()
                        notify.assert_awaited_once()
                        self.assertEqual(
                            notify.await_args.args[0],
                            esc.REASON_DEPOSIT_SENT_FOLLOWUP,
                        )

    async def test_followup_ignores_sent_keeps_armed(self):
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.mark_deposit_sent_watch_armed(5)
                context = MagicMock()
                with patch.object(
                    esc, "notify_escalation_slack", new_callable=AsyncMock
                ) as notify:
                    with patch.object(esc, "cancel_deposit_sent_watch") as cancel:
                        consumed = await esc.handle_deposit_sent_player_followup(
                            context,
                            5,
                            club_id=1,
                            title="G",
                            message_text="sent",
                            message=self._plain_text("sent"),
                        )
                        self.assertTrue(consumed)
                        cancel.assert_not_called()
                        notify.assert_not_awaited()
                        self.assertTrue(ga.deposit_sent_watch_armed(5))

    async def test_followup_noop_when_not_armed(self):
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            context = MagicMock()
            consumed = await esc.handle_deposit_sent_player_followup(
                context, 5, club_id=1, title="G"
            )
            self.assertFalse(consumed)

    async def test_claim_unbound_instant_slack(self):
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.mark_deposit_instructions_pending(9, method_slug="zelle")
                query = AsyncMock()
                query.data = esc.DEPOSIT_SENT_CALLBACK_PREFIX
                query.message = SimpleNamespace(message_id=1)
                query.answer = AsyncMock()
                query.edit_message_reply_markup = AsyncMock()
                update = SimpleNamespace(
                    callback_query=query,
                    effective_chat=SimpleNamespace(id=9, title="G"),
                )
                context = MagicMock()
                context.bot.send_message = AsyncMock()
                context.job_queue = MagicMock()

                with patch.object(esc, "get_club_for_chat", return_value=1):
                    with patch.object(
                        esc, "escalation_notification_eligible", return_value=True
                    ):
                        with patch(
                            "bot.services.payment_method_binding.get_chat_binding",
                            return_value=None,
                        ):
                            with patch.object(
                                esc,
                                "notify_escalation_slack",
                                new_callable=AsyncMock,
                            ) as notify:
                                await esc.handle_deposit_sent_claim(update, context)

                notify.assert_awaited_once()
                self.assertEqual(
                    notify.await_args.args[0], esc.REASON_DEPOSIT_SENT_UNBOUND
                )
                context.bot.send_message.assert_awaited()
                self.assertEqual(
                    context.bot.send_message.await_args.kwargs["text"],
                    esc.DEPOSIT_SENT_ACK_COPY,
                )

    async def test_claim_bound_schedules_watch(self):
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.mark_deposit_instructions_pending(11, method_slug="zelle")
                query = AsyncMock()
                query.data = esc.DEPOSIT_SENT_CALLBACK_PREFIX
                query.message = SimpleNamespace(message_id=1)
                query.answer = AsyncMock()
                query.edit_message_reply_markup = AsyncMock()
                update = SimpleNamespace(
                    callback_query=query,
                    effective_chat=SimpleNamespace(id=11, title="G"),
                )
                context = MagicMock()
                context.bot.send_message = AsyncMock()
                context.job_queue = MagicMock()

                with patch.object(esc, "get_club_for_chat", return_value=1):
                    with patch.object(
                        esc, "escalation_notification_eligible", return_value=True
                    ):
                        with patch(
                            "bot.services.payment_method_binding.get_chat_binding",
                            return_value=object(),
                        ):
                            with patch.object(esc, "schedule_deposit_sent_watch") as sched:
                                await esc.handle_deposit_sent_claim(update, context)
                                sched.assert_called_once()

    async def test_timeout_skips_when_payment_seen(self):
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                armed = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
                ga.mark_deposit_sent_watch_armed(7, armed_at=armed)
                context = MagicMock()
                context.job = SimpleNamespace(
                    data={"chat_id": 7, "club_id": 1, "title": "G"}
                )
                with patch.object(esc, "_payment_seen_since_arm", return_value=True):
                    with patch.object(
                        esc, "notify_escalation_slack", new_callable=AsyncMock
                    ) as notify:
                        with patch.object(esc, "cancel_deposit_sent_watch") as cancel:
                            await esc._deposit_sent_timeout_callback(context)
                            cancel.assert_called_once()
                            notify.assert_not_awaited()


class PersistenceReloadTests(unittest.TestCase):
    def setUp(self):
        ga.clear_activity_state_for_tests()

    def test_reload_honors_armed_at_from_row(self):
        armed = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        row = SimpleNamespace(
            id=42,
            escalation_last_human_at=None,
            escalation_last_human_role=None,
            escalation_idle_episode_fired=False,
            escalation_deposit_instructions_pending=True,
            escalation_deposit_method_slug="zelle",
            escalation_deposit_sent_armed_at=armed,
        )
        with patch.object(
            ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=row
        ):
            state = ga.reload_chat_activity_state(99)
        self.assertTrue(state.deposit_sent_watch_armed)
        self.assertEqual(state.deposit_sent_armed_at, armed)
        self.assertEqual(state.deposit_method_slug, "zelle")
        self.assertTrue(ga.deposit_sent_watch_armed(99))


if __name__ == "__main__":
    unittest.main()
