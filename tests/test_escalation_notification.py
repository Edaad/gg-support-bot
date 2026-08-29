"""Unit tests for shared group activity detection and escalation notification."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import group_activity as ga
from bot.services import escalation_notification as esc

_UNION_DEPOSIT_REQUESTED_AT = datetime(2026, 8, 28, 23, 4, 10, tzinfo=timezone.utc)
_UNION_DEPOSIT_KWARGS = {
    "method_tag": "zelle email",
    "requested_at": _UNION_DEPOSIT_REQUESTED_AT,
}


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

    def test_silence_threshold_is_five_minutes(self):
        self.assertEqual(ga.ESCALATION_SILENCE_SECONDS, 300)

    def test_null_last_human_player_fires(self):
        """Virgin / post-migrate state: first player message may escalate."""
        silence = ga.ESCALATION_SILENCE_SECONDS
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                obs = ga.record_human_message(
                    1, role="player", now=t0, silence_seconds=silence
                )
        self.assertTrue(obs.should_fire_idle)
        self.assertTrue(obs.silence_elapsed)

    def test_null_last_human_staff_does_not_fire(self):
        silence = ga.ESCALATION_SILENCE_SECONDS
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                obs = ga.record_human_message(
                    1, role="staff", now=t0, silence_seconds=silence
                )
        self.assertFalse(obs.should_fire_idle)

    def test_player_after_silence_fires_once(self):
        silence = ga.ESCALATION_SILENCE_SECONDS
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.record_human_message(1, role="player", now=t0, silence_seconds=silence)
                t1 = t0 + timedelta(seconds=silence + 1)
                obs = ga.record_human_message(
                    1, role="player", now=t1, silence_seconds=silence
                )
                self.assertTrue(obs.should_fire_idle)
                t2 = t1 + timedelta(seconds=30)
                obs2 = ga.record_human_message(
                    1, role="player", now=t2, silence_seconds=silence
                )
                self.assertFalse(obs2.should_fire_idle)

    def test_staff_then_player_without_silence_no_fire(self):
        silence = ga.ESCALATION_SILENCE_SECONDS
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.record_human_message(1, role="staff", now=t0, silence_seconds=silence)
                t1 = t0 + timedelta(seconds=30)
                obs = ga.record_human_message(
                    1, role="player", now=t1, silence_seconds=silence
                )
                self.assertFalse(obs.should_fire_idle)

    def test_staff_then_silence_then_player_fires(self):
        silence = ga.ESCALATION_SILENCE_SECONDS
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.record_human_message(1, role="staff", now=t0, silence_seconds=silence)
                t1 = t0 + timedelta(seconds=silence + 1)
                obs = ga.record_human_message(
                    1, role="player", now=t1, silence_seconds=silence
                )
                self.assertTrue(obs.should_fire_idle)

    def test_episode_resets_after_silence(self):
        silence = ga.ESCALATION_SILENCE_SECONDS
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.record_human_message(1, role="player", now=t0, silence_seconds=silence)
                t1 = t0 + timedelta(seconds=silence + 1)
                self.assertTrue(
                    ga.record_human_message(
                        1, role="player", now=t1, silence_seconds=silence
                    ).should_fire_idle
                )
                t2 = t1 + timedelta(seconds=silence + 1)
                self.assertTrue(
                    ga.record_human_message(
                        1, role="player", now=t2, silence_seconds=silence
                    ).should_fire_idle
                )

    def test_reset_idle_episode_clears_flag(self):
        silence = ga.ESCALATION_SILENCE_SECONDS
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.record_human_message(1, role="player", now=t0, silence_seconds=silence)
                t1 = t0 + timedelta(seconds=silence + 1)
                self.assertTrue(
                    ga.record_human_message(
                        1, role="player", now=t1, silence_seconds=silence
                    ).should_fire_idle
                )
                self.assertTrue(ga.get_chat_activity_state(1).idle_episode_fired)
                ga.reset_idle_episode(1)
                self.assertFalse(ga.get_chat_activity_state(1).idle_episode_fired)

    def test_reset_idle_episode_arms_immediate_followup(self):
        """After deny arm, next free text fires without waiting another silence window."""
        silence = ga.ESCALATION_SILENCE_SECONDS
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.record_human_message(1, role="player", now=t0, silence_seconds=silence)
                t1 = t0 + timedelta(seconds=silence + 1)
                self.assertTrue(
                    ga.record_human_message(
                        1, role="player", now=t1, silence_seconds=silence
                    ).should_fire_idle
                )
                ga.reset_idle_episode(1, now=t1)
                # Flow command must not consume the arm.
                t_cmd = t1 + timedelta(seconds=1)
                obs_cmd = ga.record_human_message(
                    1,
                    role="player",
                    now=t_cmd,
                    silence_seconds=silence,
                    allow_idle_fire=False,
                )
                self.assertFalse(obs_cmd.should_fire_idle)
                t_follow = t_cmd + timedelta(seconds=1)
                self.assertTrue(
                    ga.record_human_message(
                        1, role="player", now=t_follow, silence_seconds=silence
                    ).should_fire_idle
                )

    def test_flow_command_does_not_consume_idle_episode(self):
        silence = ga.ESCALATION_SILENCE_SECONDS
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                t1 = t0 + timedelta(seconds=silence + 1)
                obs = ga.record_human_message(
                    1,
                    role="player",
                    now=t1,
                    silence_seconds=silence,
                    allow_idle_fire=False,
                )
                self.assertFalse(obs.should_fire_idle)
                self.assertFalse(ga.get_chat_activity_state(1).idle_episode_fired)
                self.assertTrue(
                    ga.record_human_message(
                        1, role="player", now=t1 + timedelta(seconds=1), silence_seconds=silence
                    ).should_fire_idle
                )

    def test_post_deposit_pending_fires_without_silence(self):
        silence = ga.ESCALATION_SILENCE_SECONDS
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.record_human_message(1, role="staff", now=t0, silence_seconds=silence)
                ga.mark_post_deposit_idle_pending(1)
                self.assertTrue(ga.post_deposit_idle_pending(1))
                t1 = t0 + timedelta(seconds=30)
                obs = ga.record_human_message(
                    1, role="player", now=t1, silence_seconds=silence
                )
                self.assertTrue(obs.should_fire_idle)
                self.assertFalse(ga.post_deposit_idle_pending(1))

    def test_post_deposit_pending_survives_staff_then_player(self):
        """Holden case: staff Added chips after payment; player question still idles."""
        silence = ga.ESCALATION_SILENCE_SECONDS
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.mark_post_deposit_idle_pending(1)
                t_staff = t0 + timedelta(seconds=10)
                ga.record_human_message(
                    1, role="staff", now=t_staff, silence_seconds=silence
                )
                self.assertTrue(ga.post_deposit_idle_pending(1))
                t_player = t_staff + timedelta(seconds=30)
                obs = ga.record_human_message(
                    1, role="player", now=t_player, silence_seconds=silence
                )
                self.assertTrue(obs.should_fire_idle)
                self.assertFalse(ga.post_deposit_idle_pending(1))

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
                method_slug="venmo",
            )
        self.assertIn(
            "*Manual deposit request — no venmo binding for this group.*",
            text,
        )
        self.assertIn("`CC / 9255-5089 / Justin`", text)

    def test_unbound_manual_deposit_headline_without_slug(self):
        with patch.object(esc, "_club_display_name", return_value="Creator Club"):
            text = esc.format_escalation_slack_text(
                esc.REASON_DEPOSIT_SENT_UNBOUND,
                club_id=3,
                chat_id=-300,
                title="CC / 1 / X",
            )
        self.assertIn(
            "*Manual deposit request — no binding for the selected "
            "payment method in this group.*",
            text,
        )

    def test_deposit_sent_timeout_headline(self):
        with patch.object(esc, "_club_display_name", return_value="ClubGTO"):
            text = esc.format_escalation_slack_text(
                esc.REASON_DEPOSIT_SENT_TIMEOUT,
                club_id=1,
                chat_id=-100,
                title="GTO / 1 / Nick",
            )
        self.assertIn(
            "*5 minutes have passed since the player said they sent the payment — "
            "please look out for a payment in this group chat.*",
            text,
        )

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

    def test_deposit_player_message_copy(self):
        with patch.object(esc, "_club_display_name", return_value="Creator Club"):
            text = esc.format_escalation_slack_text(
                esc.REASON_DEPOSIT_PLAYER_MESSAGE,
                club_id=3,
                chat_id=-300,
                title="CC / 2514-2282 / Nick",
                message_text="Is 30 Venmo available for deposit",
            )
        self.assertIn("*Player messaged during deposit.*", text)
        self.assertIn("Is 30 Venmo available for deposit", text)

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

    def test_union_deposit_first_copy(self):
        with patch.object(esc, "_club_display_name", return_value="Round Table"):
            text = esc.format_union_deposit_slack_text(
                variant="first",
                club_id=2,
                chat_id=-300,
                title="RT AT / 3333-3333 / @jz034",
                amount=Decimal("500"),
                method_display_name="Zelle",
                **_UNION_DEPOSIT_KWARGS,
            )
        self.assertIn("*Union method deposit*", text)
        self.assertIn("Time: Aug 28, 2026 at 7:04 PM ET", text)
        self.assertIn("Amount: $500", text)
        self.assertIn("Tag: zelle email", text)
        self.assertNotIn("Method:", text)
        self.assertIn(
            "Verify the time, ensure payment status is visible, and if you are unsure, "
            "contact head admins.",
            text,
        )

    def test_union_deposit_repeat_verified_copy(self):
        with patch.object(esc, "_club_display_name", return_value="ClubGTO"):
            text = esc.format_union_deposit_slack_text(
                variant="repeat_verified",
                club_id=4,
                chat_id=-300,
                title="GTO / 1 / x",
                amount=Decimal("100.50"),
                method_display_name="Cash App",
                method_tag="$cashapp-tag",
                requested_at=_UNION_DEPOSIT_REQUESTED_AT,
            )
        self.assertIn("*Union method deposit*", text)
        self.assertIn("Amount: $100.50", text)
        self.assertIn("Tag: $cashapp-tag", text)

    def test_union_deposit_repeat_open_copy(self):
        with patch.object(esc, "_club_display_name", return_value="ClubGTO"):
            text = esc.format_union_deposit_slack_text(
                variant="repeat_open",
                club_id=4,
                chat_id=-300,
                title="GTO / 1 / x",
                amount=Decimal("75"),
                method_display_name="Apple Pay",
                method_tag="pay@example.com",
                requested_at=_UNION_DEPOSIT_REQUESTED_AT,
            )
        self.assertIn("*Union method deposit*", text)
        self.assertIn(
            "Verify the time, ensure payment status is visible, and if you are unsure, "
            "contact head admins.",
            text,
        )
        self.assertNotIn("Prior request still unchecked", text)

    def test_extract_player_message_prefers_text(self):
        msg = SimpleNamespace(text=" hello ", caption="cap", photo=None)
        self.assertEqual(esc.extract_player_message_for_slack(msg), "hello")

    def test_extract_player_message_media_placeholder(self):
        msg = SimpleNamespace(text=None, caption=None)
        self.assertEqual(
            esc.extract_player_message_for_slack(msg), esc.MEDIA_ONLY_PLACEHOLDER
        )


class UnionDepositSlackNotifyTests(unittest.IsolatedAsyncioTestCase):
    async def test_union_deposit_telegram_copy(self):
        with patch.object(esc, "_club_display_name", return_value="Round Table"), patch(
            "notification.formatting.resolve_and_format_group_chat_line",
            new_callable=AsyncMock,
            return_value="Group Chat: RT / 1234-5678 / Player",
        ):
            text = await esc.format_union_deposit_telegram_text(
                variant="first",
                club_id=2,
                chat_id=-300,
                title="RT / 1234-5678 / Player",
                amount=Decimal("500"),
                method_display_name="Zelle",
                **_UNION_DEPOSIT_KWARGS,
            )
        self.assertIn("<b>Union method deposit</b>", text)
        self.assertIn("Group Chat: RT / 1234-5678 / Player", text)
        self.assertIn("Player ID: <code>1234-5678</code>", text)
        self.assertIn("Time: Aug 28, 2026 at 7:04 PM ET", text)
        self.assertIn("Amount: $500", text)
        self.assertIn("Tag: zelle email", text)

    async def test_skips_on_test_bot(self):
        with patch.object(esc, "is_test_bot_worker", return_value=True):
            ok = await esc.notify_union_deposit_request_slack(
                variant="first",
                club_id=2,
                chat_id=-100,
                title="RT / 1 / x",
                amount=Decimal("100"),
                method_display_name="Zelle",
                method_tag="zelle email",
                requested_at=_UNION_DEPOSIT_REQUESTED_AT,
            )
        self.assertFalse(ok)

    async def test_skips_when_not_eligible(self):
        with patch.object(esc, "is_test_bot_worker", return_value=False), patch.object(
            esc, "escalation_notification_eligible", return_value=False
        ):
            ok = await esc.notify_union_deposit_request_slack(
                variant="first",
                club_id=2,
                chat_id=-100,
                title="RT / 1 / x",
                amount=Decimal("100"),
                method_display_name="Zelle",
                method_tag="zelle email",
                requested_at=_UNION_DEPOSIT_REQUESTED_AT,
            )
        self.assertFalse(ok)

    async def test_first_uses_first_reason(self):
        with patch.object(esc, "is_test_bot_worker", return_value=False), patch.object(
            esc, "escalation_notification_eligible", return_value=True
        ), patch.object(
            esc,
            "format_union_deposit_slack_text",
            return_value="First-time union deposit body",
        ), patch.object(
            esc,
            "format_union_deposit_telegram_text",
            new_callable=AsyncMock,
            return_value="<b>First-time union deposit body</b>",
        ), patch.object(
            esc,
            "_notify_union_deposit_payment_chats",
            new_callable=AsyncMock,
        ) as payment_chats, patch.object(
            esc, "notify_escalation_slack", new_callable=AsyncMock, return_value=True
        ) as notify:
            ok = await esc.notify_union_deposit_request_slack(
                variant="first",
                club_id=2,
                chat_id=-100,
                title="RT / 1 / x",
                amount=Decimal("100"),
                method_display_name="Zelle",
                method_tag="zelle email",
                requested_at=_UNION_DEPOSIT_REQUESTED_AT,
            )
        self.assertTrue(ok)
        payment_chats.assert_awaited_once()
        notify.assert_awaited_once()
        self.assertEqual(notify.await_args.args[0], esc.REASON_UNION_DEPOSIT_FIRST)
        self.assertEqual(
            notify.await_args.kwargs["slack_text"],
            "First-time union deposit body",
        )

    async def test_repeat_uses_repeat_reason(self):
        with patch.object(esc, "is_test_bot_worker", return_value=False), patch.object(
            esc, "escalation_notification_eligible", return_value=True
        ), patch.object(
            esc,
            "format_union_deposit_slack_text",
            return_value="Repeat union deposit body",
        ), patch.object(
            esc,
            "format_union_deposit_telegram_text",
            new_callable=AsyncMock,
            return_value="<b>Repeat union deposit body</b>",
        ), patch.object(
            esc,
            "_notify_union_deposit_payment_chats",
            new_callable=AsyncMock,
        ), patch.object(
            esc, "notify_escalation_slack", new_callable=AsyncMock, return_value=True
        ) as notify:
            await esc.notify_union_deposit_request_slack(
                variant="repeat_verified",
                club_id=2,
                chat_id=-100,
                title="RT / 1 / x",
                amount=Decimal("100"),
                method_display_name="Zelle",
                method_tag="zelle email",
                requested_at=_UNION_DEPOSIT_REQUESTED_AT,
            )
        self.assertEqual(notify.await_args.args[0], esc.REASON_UNION_DEPOSIT_REPEAT)

    async def test_payment_chat_routing_uses_group_title(self):
        with patch(
            "notification.payment_notification_routing.resolve_notification_chat_ids",
            return_value=[-9001],
        ) as resolve, patch(
            "notification.payment_notification_delivery.deliver_payment_notification",
            new_callable=AsyncMock,
            return_value=[(-9001, 42)],
        ) as deliver:
            await esc._notify_union_deposit_payment_chats(
                telegram_text="<b>Union deposit</b>",
                group_title="GTO / 1 / x",
            )
        resolve.assert_called_once_with(["GTO / 1 / x"])
        deliver.assert_awaited_once_with(
            "<b>Union deposit</b>",
            bind_chat_ids=[-9001],
            include_slack_escalation=False,
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


class DepositPlayerMessageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ga.clear_activity_state_for_tests()

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

    def test_valid_amount_skipped(self):
        context = MagicMock()
        context.chat_data = {
            "deposit_club_id": 1,
            "deposit_awaiting_amount": True,
        }
        self.assertTrue(
            esc.is_valid_deposit_flow_answer(context, self._text_msg("33"))
        )

    def test_amount_still_valid_after_handler_stores_amount(self):
        """group_activity runs after deposit_amount_received mutates chat_data."""
        context = MagicMock()
        context.chat_data = {
            "deposit_club_id": 1,
            "deposit_amount": 100,
            "deposit_amount_message_id": 55,
        }
        matched = self._text_msg("100")
        matched.message_id = 55
        self.assertTrue(esc.is_valid_deposit_flow_answer(context, matched))
        # Different message after amount is stored (choose-method) is not a flow answer
        other = self._text_msg("100.00")
        other.message_id = 56
        self.assertFalse(esc.is_valid_deposit_flow_answer(context, other))

    def test_venmo_question_not_valid_answer(self):
        context = MagicMock()
        context.chat_data = {
            "deposit_club_id": 1,
            "deposit_amount": 33,
        }
        self.assertFalse(
            esc.is_valid_deposit_flow_answer(
                context, self._text_msg("Is 30 Venmo available for deposit")
            )
        )

    async def test_method_picker_question_escalates(self):
        context = MagicMock()
        context.chat_data = {
            "deposit_club_id": 1,
            "deposit_amount": 33,
        }
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                with patch.object(
                    esc, "notify_escalation_slack", new_callable=AsyncMock
                ) as notify:
                    consumed = await esc.handle_deposit_player_message(
                        context,
                        99,
                        club_id=3,
                        title="CC / 2514-2282 / Nick",
                        message_text="Is 30 Venmo available for deposit",
                        message=self._text_msg(
                            "Is 30 Venmo available for deposit"
                        ),
                    )
        self.assertTrue(consumed)
        notify.assert_awaited_once()
        self.assertEqual(
            notify.await_args.args[0], esc.REASON_DEPOSIT_PLAYER_MESSAGE
        )

    async def test_valid_amount_does_not_escalate(self):
        context = MagicMock()
        context.chat_data = {
            "deposit_club_id": 1,
            "deposit_awaiting_amount": True,
        }
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                with patch.object(
                    esc, "notify_escalation_slack", new_callable=AsyncMock
                ) as notify:
                    consumed = await esc.handle_deposit_player_message(
                        context,
                        99,
                        club_id=3,
                        title="G",
                        message_text="33",
                        message=self._text_msg("33"),
                    )
        self.assertFalse(consumed)
        notify.assert_not_awaited()

    async def test_amount_after_stored_at_choose_escalates(self):
        """Bare numbers on choose-method are chatter, not amount answers."""
        context = MagicMock()
        context.chat_data = {
            "deposit_club_id": 1,
            "deposit_amount": 100,
        }
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                with patch.object(
                    esc, "notify_escalation_slack", new_callable=AsyncMock
                ) as notify:
                    consumed = await esc.handle_deposit_player_message(
                        context,
                        99,
                        club_id=3,
                        title="G",
                        message_text="100",
                        message=self._text_msg("100"),
                    )
        self.assertTrue(consumed)
        notify.assert_awaited_once()
        self.assertEqual(
            notify.await_args.args[0], esc.REASON_DEPOSIT_PLAYER_MESSAGE
        )

    async def test_amount_entry_message_id_does_not_escalate(self):
        """Same update that stored deposit_amount must not Slack via group_activity."""
        context = MagicMock()
        context.chat_data = {
            "deposit_club_id": 1,
            "deposit_amount": 100,
            "deposit_amount_message_id": 55,
        }
        msg = self._text_msg("100")
        msg.message_id = 55
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                with patch.object(
                    esc, "notify_escalation_slack", new_callable=AsyncMock
                ) as notify:
                    consumed = await esc.handle_deposit_player_message(
                        context,
                        99,
                        club_id=3,
                        title="G",
                        message_text="100",
                        message=msg,
                    )
        self.assertFalse(consumed)
        notify.assert_not_awaited()

    async def test_armed_wait_defers_to_followup_path(self):
        context = MagicMock()
        context.chat_data = {"deposit_club_id": 1, "deposit_amount": 33}
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.mark_deposit_sent_watch_armed(99)
                with patch.object(
                    esc, "notify_escalation_slack", new_callable=AsyncMock
                ) as notify:
                    consumed = await esc.handle_deposit_player_message(
                        context,
                        99,
                        club_id=3,
                        title="G",
                        message_text="where are chips",
                        message=self._text_msg("where are chips"),
                    )
        self.assertFalse(consumed)
        notify.assert_not_awaited()

    async def test_sent_ack_does_not_escalate(self):
        context = MagicMock()
        context.chat_data = {
            "deposit_club_id": 1,
            "deposit_amount": 33,
        }
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                with patch.object(
                    esc, "notify_escalation_slack", new_callable=AsyncMock
                ) as notify:
                    consumed = await esc.handle_deposit_player_message(
                        context,
                        99,
                        club_id=3,
                        title="GTO / 1399-7800 / @Andrew_2256",
                        message_text="Sent",
                        message=self._text_msg("Sent"),
                    )
        self.assertFalse(consumed)
        notify.assert_not_awaited()

    async def test_media_does_not_escalate(self):
        context = MagicMock()
        context.chat_data = {
            "deposit_club_id": 1,
            "deposit_amount": 33,
        }
        msg = MagicMock()
        msg.text = None
        msg.caption = None
        msg.photo = [object()]
        msg.message_id = 10
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                with patch.object(
                    esc, "notify_escalation_slack", new_callable=AsyncMock
                ) as notify:
                    consumed = await esc.handle_deposit_player_message(
                        context,
                        99,
                        club_id=3,
                        title="G",
                        message_text="(media)",
                        message=msg,
                    )
        self.assertFalse(consumed)
        notify.assert_not_awaited()


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
                        # Not consumed: player-idle may still fire.
                        self.assertFalse(consumed)
                        cancel.assert_not_called()
                        notify.assert_not_awaited()
                        self.assertTrue(ga.deposit_sent_watch_armed(5))

    async def test_followup_ignores_media_does_not_block_idle(self):
        media_msg = SimpleNamespace(
            text="I'm depositing 106$ more",
            caption=None,
            photo=[object()],
            video=None,
            document=None,
            animation=None,
            voice=None,
            video_note=None,
            audio=None,
            sticker=None,
        )
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
                            message_text="I'm depositing 106$ more",
                            message=media_msg,
                        )
                        self.assertFalse(consumed)
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
                            "bot.services.payment_method_binding.chat_has_deposit_method_binding",
                            return_value=False,
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
                            "bot.services.payment_method_binding.chat_has_deposit_method_binding",
                            return_value=True,
                        ):
                            with patch.object(esc, "schedule_deposit_sent_watch") as sched:
                                await esc.handle_deposit_sent_claim(update, context)
                                sched.assert_called_once()

    async def test_claim_crypto_wallet_binding_schedules_watch(self):
        """Crypto bound via crypto_wallet_bindings must not Slack unbound."""
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.mark_deposit_instructions_pending(12, method_slug="crypto")
                query = AsyncMock()
                query.data = esc.DEPOSIT_SENT_CALLBACK_PREFIX
                query.message = SimpleNamespace(message_id=1)
                query.answer = AsyncMock()
                query.edit_message_reply_markup = AsyncMock()
                update = SimpleNamespace(
                    callback_query=query,
                    effective_chat=SimpleNamespace(id=12, title="G"),
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
                            with patch(
                                "bot.services.payment_method_binding.chat_has_crypto_wallet_binding",
                                return_value=True,
                            ):
                                with patch.object(
                                    esc,
                                    "notify_escalation_slack",
                                    new_callable=AsyncMock,
                                ) as notify:
                                    with patch.object(
                                        esc, "schedule_deposit_sent_watch"
                                    ) as sched:
                                        await esc.handle_deposit_sent_claim(
                                            update, context
                                        )
                notify.assert_not_awaited()
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


    async def test_offer_button_allows_stripe(self):
        bot = MagicMock()
        bot.edit_message_reply_markup = AsyncMock()
        bot.send_message = AsyncMock()
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                with patch.object(
                    esc, "escalation_notification_eligible", return_value=True
                ):
                    offered = await esc.offer_deposit_sent_button(
                        bot,
                        42,
                        club_id=1,
                        method_slug="stripe",
                        title="G",
                        attach_to_message_id=7,
                    )
        self.assertTrue(offered)
        bot.edit_message_reply_markup.assert_awaited_once()
        self.assertTrue(ga.deposit_instructions_pending(42))
        self.assertEqual(ga.deposit_sent_button_message_id(42), 7)

    async def test_payment_clears_chase_and_strips_button(self):
        bot = MagicMock()
        bot.edit_message_reply_markup = AsyncMock()
        with patch.object(ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None):
            with patch.object(ga, "_persist_activity_state"):
                ga.mark_deposit_instructions_pending(55, method_slug="zelle")
                ga.set_deposit_sent_button_message_id(55, 99)
                await esc.clear_deposit_chase_after_payment(bot, 55)
        self.assertFalse(ga.deposit_instructions_pending(55))
        self.assertIsNone(ga.deposit_sent_button_message_id(55))
        self.assertTrue(ga.post_deposit_idle_pending(55))
        bot.edit_message_reply_markup.assert_awaited_once_with(
            chat_id=55,
            message_id=99,
            reply_markup=None,
        )


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
            escalation_deposit_sent_button_message_id=None,
            escalation_post_deposit_idle_pending=True,
        )
        with patch.object(
            ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=row
        ):
            state = ga.reload_chat_activity_state(99)
            self.assertTrue(state.deposit_sent_watch_armed)
            self.assertEqual(state.deposit_sent_armed_at, armed)
            self.assertEqual(state.deposit_method_slug, "zelle")
            self.assertTrue(ga.deposit_sent_watch_armed(99))
            self.assertTrue(state.post_deposit_idle_pending)


if __name__ == "__main__":
    unittest.main()
