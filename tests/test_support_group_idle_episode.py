"""Tests for support-group idle episode (1m burst / 5m silence / 30m cap)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import support_group_idle_episode as ep
from bot.services import escalation_notification as esc


class _FakeRow:
    def __init__(self, chat_id: int):
        self.telegram_chat_id = chat_id
        self.title = None
        self.episode_started_at = None
        self.last_human_at = None
        self.burst_json = []
        self.history_episode_id = None
        self.updated_at = None


class _FakeSession:
    store: dict[int, _FakeRow] = {}

    def get(self, _model, key):
        try:
            return self.store.get(int(key))
        except (TypeError, ValueError):
            return None

    def add(self, row):
        if not hasattr(row, "episode_started_at"):
            return
        self.store[int(row.telegram_chat_id)] = row

    def query(self, _model):
        session = self

        class _Q:
            def filter(self, *_a, **_k):
                return self

            def all(self):
                return [
                    r
                    for r in session.store.values()
                    if r.episode_started_at is not None
                ]

        return _Q()


class _FakeDbCtx:
    def __enter__(self):
        return _FakeSession()

    def __exit__(self, *exc):
        return False


def _job_queue() -> MagicMock:
    jq = MagicMock()
    jq.get_jobs_by_name.return_value = []
    jq.run_once = MagicMock()
    return jq


class ExpectedInputMarkTests(unittest.TestCase):
    def test_mark_and_consume(self):
        ctx = SimpleNamespace(chat_data={})
        self.assertFalse(ep.consume_expected_flow_input(ctx))
        ep.mark_expected_flow_input(ctx)
        self.assertTrue(ep.consume_expected_flow_input(ctx))
        self.assertFalse(ep.consume_expected_flow_input(ctx))


class IdleEpisodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _FakeSession.store = {}
        self.jq = _job_queue()
        self.db_patch = patch.object(ep, "get_db", side_effect=_FakeDbCtx)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.debounce_patch = patch.object(
            ep, "awaiting_agent_debounce_seconds", return_value=60
        )
        self.debounce_patch.start()
        self.addCleanup(self.debounce_patch.stop)
        self.hardcap_patch = patch.object(
            ep, "idle_episode_hard_cap_seconds", return_value=1800
        )
        self.hardcap_patch.start()
        self.addCleanup(self.hardcap_patch.stop)
        self.silence_patch = patch.object(
            ep, "idle_episode_silence_seconds", return_value=300
        )
        self.silence_patch.start()
        self.addCleanup(self.silence_patch.stop)
        self.close_hist_patch = patch.object(ep, "close_history_episode")
        self.close_hist_patch.start()
        self.addCleanup(self.close_hist_patch.stop)

    async def test_open_slacks_once_and_schedules_timers(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(
            ep, "notify_escalation_slack", new_callable=AsyncMock, return_value=(True, 1)
        ) as slack:
            with patch.object(
                ep, "offer_idle_help_prompt", new_callable=AsyncMock, return_value=False
            ) as menu:
                result = await ep.on_player_reach_out(
                    1,
                    club_id=9,
                    title="GC",
                    message_text="hello",
                    job_queue=self.jq,
                    now=t0,
                )
        self.assertEqual(result.outcome, "opened")
        slack.assert_awaited_once()
        self.assertEqual(slack.await_args.args[0], esc.REASON_PLAYER_IDLE)
        menu.assert_awaited_once()
        state = ep.load_episode_state(1)
        self.assertIsNotNone(state)
        self.assertEqual(state["burst"], [])
        names = [c.kwargs.get("name") for c in self.jq.run_once.call_args_list]
        self.assertIn(ep._silence_job_name(1), names)
        self.assertIn(ep._hardcap_job_name(1), names)
        self.assertNotIn(ep._debounce_job_name(1), names)

    async def test_feed_schedules_debounce(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(
            ep, "notify_escalation_slack", new_callable=AsyncMock, return_value=(True, 1)
        ):
            with patch.object(
                ep, "offer_idle_help_prompt", new_callable=AsyncMock, return_value=False
            ):
                await ep.on_player_reach_out(
                    1,
                    club_id=9,
                    message_text="hello",
                    job_queue=self.jq,
                    now=t0,
                )
        self.jq.run_once.reset_mock()
        t1 = t0 + timedelta(seconds=10)
        with patch.object(
            ep, "notify_escalation_slack", new_callable=AsyncMock, return_value=(True, 2)
        ) as slack:
            result = await ep.on_player_reach_out(
                1,
                club_id=9,
                message_text="still here",
                job_queue=self.jq,
                now=t1,
            )
        self.assertEqual(result.outcome, "fed")
        slack.assert_not_called()
        state = ep.load_episode_state(1)
        self.assertEqual(len(state["burst"]), 1)
        self.assertEqual(state["burst"][0]["text"], "still here")
        names = [c.kwargs.get("name") for c in self.jq.run_once.call_args_list]
        self.assertIn(ep._debounce_job_name(1), names)

    async def test_deposit_feed_opens_without_second_slack(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(
            ep, "notify_escalation_slack", new_callable=AsyncMock
        ) as slack:
            with patch.object(
                ep, "offer_idle_help_prompt", new_callable=AsyncMock, return_value=False
            ):
                await ep.feed_or_open_episode(
                    1,
                    club_id=9,
                    message_text="where is venmo?",
                    slack_already_sent=True,
                    job_queue=self.jq,
                    now=t0,
                )
        slack.assert_not_called()
        state = ep.load_episode_state(1)
        self.assertIsNotNone(state)
        self.assertEqual(state["burst"][0]["text"], "where is venmo?")
        names = [c.kwargs.get("name") for c in self.jq.run_once.call_args_list]
        self.assertIn(ep._debounce_job_name(1), names)

    def test_staff_clears_burst(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        _FakeSession.store[1] = _FakeRow(1)
        row = _FakeSession.store[1]
        row.episode_started_at = t0
        row.last_human_at = t0
        row.burst_json = [{"text": "ping"}]
        ep.on_staff_human(1, job_queue=self.jq, now=t0 + timedelta(seconds=5))
        state = ep.load_episode_state(1)
        self.assertIsNotNone(state)
        self.assertEqual(state["burst"], [])
        names = [c.kwargs.get("name") for c in self.jq.run_once.call_args_list]
        self.assertIn(ep._silence_job_name(1), names)

    def test_close_episode_clears_row(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        _FakeSession.store[1] = _FakeRow(1)
        row = _FakeSession.store[1]
        row.episode_started_at = t0
        row.last_human_at = t0
        row.burst_json = [{"text": "x"}]
        ep.close_episode(1, job_queue=self.jq, close_reason=ep.CLOSE_REASON_SILENCE)
        self.assertIsNone(ep.load_episode_state(1))
        ep.close_history_episode.assert_called()

    async def test_debounce_slacks_followup_and_clears_burst(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        _FakeSession.store[1] = _FakeRow(1)
        row = _FakeSession.store[1]
        row.episode_started_at = t0
        row.last_human_at = t0
        row.burst_json = [{"text": "follow"}]
        row.title = "GC"

        ctx = SimpleNamespace(
            job=SimpleNamespace(
                data={"chat_id": 1, "club_id": 9, "title": "GC"},
                chat_id=1,
            ),
            job_queue=self.jq,
        )
        with patch.object(ep, "_now", return_value=t0 + timedelta(seconds=61)):
            with patch.object(
                ep, "notify_escalation_slack", new_callable=AsyncMock
            ) as slack:
                await ep._idle_debounce_callback(ctx)
        slack.assert_awaited_once()
        self.assertEqual(slack.await_args.args[0], esc.REASON_PLAYER_IDLE_FOLLOWUP)
        state = ep.load_episode_state(1)
        self.assertIsNotNone(state)
        self.assertEqual(state["burst"], [])

    async def test_silence_closes(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        _FakeSession.store[1] = _FakeRow(1)
        row = _FakeSession.store[1]
        row.episode_started_at = t0
        row.last_human_at = t0
        ctx = SimpleNamespace(
            job=SimpleNamespace(data={"chat_id": 1}, chat_id=1),
            job_queue=self.jq,
        )
        with patch.object(ep, "_now", return_value=t0 + timedelta(seconds=301)):
            await ep._idle_silence_callback(ctx)
        self.assertIsNone(ep.load_episode_state(1))

    async def test_hardcap_closes(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        _FakeSession.store[1] = _FakeRow(1)
        row = _FakeSession.store[1]
        row.episode_started_at = t0
        row.last_human_at = t0
        ctx = SimpleNamespace(
            job=SimpleNamespace(data={"chat_id": 1}, chat_id=1),
            job_queue=self.jq,
        )
        await ep._idle_hardcap_callback(ctx)
        self.assertIsNone(ep.load_episode_state(1))

    def test_restore_closes_expired_silence(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        _FakeSession.store[1] = _FakeRow(1)
        row = _FakeSession.store[1]
        row.episode_started_at = t0
        row.last_human_at = t0
        with patch.object(ep, "_now", return_value=t0 + timedelta(seconds=400)):
            ep.restore_support_group_idle_episode_jobs(self.jq)
        self.assertIsNone(ep.load_episode_state(1))

    def test_restore_reschedules_remaining(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        now = t0 + timedelta(seconds=30)
        _FakeSession.store[1] = _FakeRow(1)
        row = _FakeSession.store[1]
        row.episode_started_at = t0
        row.last_human_at = now - timedelta(seconds=10)
        row.burst_json = [{"text": "a"}]
        with patch.object(ep, "_now", return_value=now):
            ep.restore_support_group_idle_episode_jobs(self.jq)
        names = [c.kwargs.get("name") for c in self.jq.run_once.call_args_list]
        self.assertIn(ep._hardcap_job_name(1), names)
        self.assertIn(ep._silence_job_name(1), names)
        self.assertIn(ep._debounce_job_name(1), names)


if __name__ == "__main__":
    unittest.main()
