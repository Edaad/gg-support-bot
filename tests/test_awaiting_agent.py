"""Tests for awaiting-agent 1m debounce after Talk to agent."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers import group_activity as ga_handler
from bot.services import escalation_notification as esc
from bot.services import group_activity as ga
from bot.services import popup_keyboard as pk


def _job_queue() -> MagicMock:
    jq = MagicMock()
    jq.get_jobs_by_name.return_value = []
    jq.run_once = MagicMock()
    return jq


class AwaitingAgentEpisodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ga.clear_activity_state_for_tests()
        esc.clear_awaiting_agent_state_for_tests()

    def test_start_schedules_episode_and_debounce(self):
        jq = _job_queue()
        esc.start_awaiting_agent_episode(
            99,
            club_id=3,
            title="CC / 1 / P",
            message_text="need help",
            job_queue=jq,
        )
        self.assertTrue(esc.awaiting_agent_episode_active(99))
        self.assertEqual(jq.run_once.call_count, 2)
        callbacks = [c.args[0] for c in jq.run_once.call_args_list]
        self.assertIn(esc._awaiting_agent_debounce_callback, callbacks)
        self.assertIn(esc._awaiting_agent_episode_end_callback, callbacks)
        delays = {c.kwargs["when"] for c in jq.run_once.call_args_list}
        self.assertEqual(
            delays,
            {
                float(esc.awaiting_agent_debounce_seconds()),
                float(esc.awaiting_agent_episode_seconds()),
            },
        )
        self.assertEqual(
            esc._burst_message_text(99),
            "need help",
        )

    def test_player_resets_debounce_and_accumulates(self):
        jq = _job_queue()
        esc.start_awaiting_agent_episode(
            99, club_id=3, title="G", message_text="first", job_queue=jq
        )
        jq.run_once.reset_mock()
        jq.get_jobs_by_name.return_value = []
        ok = esc.on_player_during_awaiting_agent(
            99, message_text="second", job_queue=jq
        )
        self.assertTrue(ok)
        self.assertEqual(esc._burst_message_text(99), "first\nsecond")
        jq.run_once.assert_called_once()
        self.assertIs(
            jq.run_once.call_args.args[0],
            esc._awaiting_agent_debounce_callback,
        )

    def test_staff_cancels_debounce_keeps_episode(self):
        jq = _job_queue()
        pending = MagicMock()
        jq.get_jobs_by_name.side_effect = lambda name: (
            [pending]
            if name == esc._awaiting_agent_debounce_job_name(99)
            else []
        )
        esc.start_awaiting_agent_episode(
            99, club_id=3, title="G", message_text="yo", job_queue=jq
        )
        ok = esc.on_staff_during_awaiting_agent(99, job_queue=jq)
        self.assertTrue(ok)
        self.assertTrue(esc.awaiting_agent_episode_active(99))
        self.assertIsNone(esc._burst_message_text(99))
        pending.schedule_removal.assert_called()

    async def test_debounce_callback_slacks_and_clears_burst(self):
        jq = _job_queue()
        esc.start_awaiting_agent_episode(
            99,
            club_id=3,
            title="CC / 1 / P",
            message_text="hello",
            job_queue=jq,
        )
        esc.on_player_during_awaiting_agent(
            99, message_text="still here", job_queue=jq
        )
        job = MagicMock()
        job.data = {"chat_id": 99}
        job.chat_id = 99
        context = MagicMock()
        context.job = job
        context.job_queue = jq

        with patch.object(
            esc, "notify_escalation_slack", new_callable=AsyncMock
        ) as slack:
            await esc._awaiting_agent_debounce_callback(context)

        slack.assert_awaited_once()
        self.assertEqual(
            slack.await_args.args[0], esc.REASON_AWAITING_AGENT_TIMEOUT
        )
        self.assertEqual(
            slack.await_args.kwargs["message_text"], "hello\nstill here"
        )
        self.assertTrue(esc.awaiting_agent_episode_active(99))
        self.assertIsNone(esc._burst_message_text(99))

    async def test_second_burst_can_fire_again(self):
        jq = _job_queue()
        esc.start_awaiting_agent_episode(
            99, club_id=3, title="G", message_text="a", job_queue=jq
        )
        job = MagicMock(data={"chat_id": 99}, chat_id=99)
        context = MagicMock(job=job, job_queue=jq)
        with patch.object(esc, "notify_escalation_slack", new_callable=AsyncMock):
            await esc._awaiting_agent_debounce_callback(context)

        esc.on_player_during_awaiting_agent(
            99, message_text="later", job_queue=jq
        )
        with patch.object(
            esc, "notify_escalation_slack", new_callable=AsyncMock
        ) as slack:
            await esc._awaiting_agent_debounce_callback(context)
        slack.assert_awaited_once()
        self.assertEqual(slack.await_args.kwargs["message_text"], "later")

    async def test_episode_end_clears_state(self):
        jq = _job_queue()
        esc.start_awaiting_agent_episode(
            99, club_id=3, title="G", message_text="a", job_queue=jq
        )
        job = MagicMock(data={"chat_id": 99}, chat_id=99)
        context = MagicMock(job=job, job_queue=jq)
        await esc._awaiting_agent_episode_end_callback(context)
        self.assertFalse(esc.awaiting_agent_episode_active(99))

    async def test_complete_idle_help_starts_episode(self):
        bot = MagicMock()
        bot.send_message = AsyncMock()
        context = MagicMock()
        context.chat_data = {}
        context.job_queue = _job_queue()

        with patch.object(
            esc, "notify_escalation_slack", new_callable=AsyncMock
        ):
            await esc.complete_idle_help_as_agent(
                bot,
                99,
                context,
                message_text="reach out",
                club_id=3,
                title="G",
            )

        self.assertTrue(esc.awaiting_agent_episode_active(99))
        self.assertEqual(esc._burst_message_text(99), "reach out")


class AwaitingAgentGroupActivityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ga.clear_activity_state_for_tests()
        esc.clear_awaiting_agent_state_for_tests()

    async def test_suppresses_idle_while_episode_open(self):
        esc.start_awaiting_agent_episode(
            -100,
            club_id=1,
            title="CC / 1 / P",
            message_text="seed",
            job_queue=_job_queue(),
        )
        user = SimpleNamespace(id=55, is_bot=False, username="p")
        chat = SimpleNamespace(id=-100, type="supergroup", title="CC / 1 / P")
        message = SimpleNamespace(
            message_id=2,
            text="bump",
            photo=None,
            video=None,
            document=None,
            animation=None,
            voice=None,
            video_note=None,
            audio=None,
            sticker=None,
            caption=None,
        )
        update = MagicMock()
        update.effective_message = message
        update.effective_chat = chat
        update.effective_user = user
        context = MagicMock()
        context.chat_data = {}
        context.bot = MagicMock()
        context.job_queue = _job_queue()

        with patch.object(ga_handler, "get_club_for_chat", return_value=1):
            with patch.object(pk, "popup_keyboard_eligible", return_value=False):
                with patch.object(
                    esc, "escalation_notification_eligible", return_value=True
                ):
                    with patch.object(ga, "is_support_sender", return_value=False):
                        with patch.object(pk, "upsert_player_telegram_user_id"):
                            with patch.object(pk, "remember_player_message"):
                                with patch.object(
                                    ga,
                                    "record_human_message",
                                    return_value=SimpleNamespace(
                                        should_fire_idle=True
                                    ),
                                ):
                                    with patch.object(
                                        esc,
                                        "handle_deposit_sent_player_followup",
                                        new_callable=AsyncMock,
                                        return_value=False,
                                    ):
                                        with patch.object(
                                            esc,
                                            "handle_deposit_player_message",
                                            new_callable=AsyncMock,
                                            return_value=False,
                                        ):
                                            with patch.object(
                                                esc,
                                                "handle_idle_help_free_text",
                                                new_callable=AsyncMock,
                                                return_value=False,
                                            ):
                                                with patch.object(
                                                    esc,
                                                    "fire_player_idle",
                                                    new_callable=AsyncMock,
                                                ) as fire:
                                                    await ga_handler.group_activity_handler(
                                                        update, context
                                                    )

        fire.assert_not_awaited()
        self.assertEqual(esc._burst_message_text(-100), "seed\nbump")

    async def test_staff_clears_burst_via_group_activity(self):
        jq = _job_queue()
        esc.start_awaiting_agent_episode(
            -100,
            club_id=1,
            title="G",
            message_text="yo",
            job_queue=jq,
        )
        user = SimpleNamespace(id=1, is_bot=False, username="am")
        chat = SimpleNamespace(id=-100, type="supergroup", title="G")
        message = SimpleNamespace(
            message_id=3,
            text="on it",
            photo=None,
            video=None,
            document=None,
            animation=None,
            voice=None,
            video_note=None,
            audio=None,
            sticker=None,
            caption=None,
        )
        update = MagicMock()
        update.effective_message = message
        update.effective_chat = chat
        update.effective_user = user
        context = MagicMock(chat_data={}, bot=MagicMock(), job_queue=jq)

        with patch.object(ga_handler, "get_club_for_chat", return_value=1):
            with patch.object(pk, "popup_keyboard_eligible", return_value=False):
                with patch.object(
                    esc, "escalation_notification_eligible", return_value=True
                ):
                    with patch.object(ga, "is_support_sender", return_value=True):
                        with patch.object(
                            ga,
                            "record_human_message",
                            return_value=SimpleNamespace(should_fire_idle=False),
                        ):
                            await ga_handler.group_activity_handler(update, context)

        self.assertTrue(esc.awaiting_agent_episode_active(-100))
        self.assertIsNone(esc._burst_message_text(-100))


if __name__ == "__main__":
    unittest.main()
