"""Tests for escalation event / episode persistence."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import escalation_notification as esc
from bot.services import escalation_observability as obs
from bot.services import support_group_idle_episode as ep


class _EventStore:
    def __init__(self):
        self.events: dict[int, MagicMock] = {}
        self.episodes: dict = {}
        self.decisions: dict[int, object] = {}
        self._next = 1
        self._next_dec = 1

    def get(self, model, key):
        if model is obs.EscalationEvent:
            return self.events.get(int(key))
        if model is obs.EscalationEpisode:
            return self.episodes.get(key)
        if model is obs.EscalationDecisionLog:
            return self.decisions.get(int(key))
        if model is obs.SupportGroupIdleEpisodeState:
            return None
        return None

    def add(self, row):
        name = row.__class__.__name__
        if isinstance(row, obs.EscalationEvent) or name == "EscalationEvent":
            row.id = self._next
            self._next += 1
            self.events[int(row.id)] = row
        elif isinstance(row, obs.EscalationEpisode) or name == "EscalationEpisode":
            self.episodes[row.id] = row
        elif isinstance(row, obs.EscalationDecisionLog) or name == "EscalationDecisionLog":
            row.id = self._next_dec
            self._next_dec += 1
            self.decisions[int(row.id)] = row

    def flush(self):
        return None


class _DbCtx:
    def __init__(self, store: _EventStore):
        self.store = store

    def __enter__(self):
        return self.store

    def __exit__(self, *exc):
        return False


class TriggerMessageTests(unittest.TestCase):
    def test_from_telegram_message(self):
        msg = SimpleNamespace(
            message_id=44,
            text="hello there",
            caption=None,
            date=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
            from_user=SimpleNamespace(
                id=7, username="jz034", first_name="J", last_name="Z"
            ),
            photo=None,
            video=None,
            document=None,
            animation=None,
            voice=None,
            video_note=None,
            audio=None,
            sticker=None,
        )
        entry = obs.trigger_message_from_telegram(msg)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["telegram_message_id"], 44)
        self.assertEqual(entry["telegram_user_id"], 7)
        self.assertEqual(entry["username"], "jz034")
        self.assertEqual(entry["display_name"], "J Z")
        self.assertEqual(entry["text"], "hello there")
        self.assertFalse(entry["has_media"])


class RecordEventTests(unittest.TestCase):
    def test_record_and_update_slack_ok(self):
        store = _EventStore()
        with patch.object(obs, "get_db", side_effect=lambda: _DbCtx(store)):
            eid = obs.record_escalation_event(
                reason=esc.REASON_CASHOUT_STARTED,
                telegram_chat_id=-100,
                club_id=2,
                group_title="G",
                slack_ok=False,
            )
            self.assertEqual(eid, 1)
            obs.update_escalation_event_slack_ok(eid, True)
        self.assertTrue(store.events[1].slack_ok)

    def test_record_never_raises(self):
        with patch.object(obs, "get_db", side_effect=RuntimeError("db down")):
            self.assertIsNone(
                obs.record_escalation_event(
                    reason="player_idle",
                    telegram_chat_id=1,
                )
            )


class RecordDecisionTests(unittest.TestCase):
    def test_record_decision(self):
        store = _EventStore()
        with patch.object(obs, "get_db", side_effect=lambda: _DbCtx(store)):
            did = obs.record_escalation_decision(
                decision=obs.DECISION_SKIPPED,
                reason=obs.REASON_ESC_OFF,
                telegram_chat_id=-100,
                club_id=2,
                group_title="G",
                telegram_user_id=7,
                role="player",
                telegram_message_id=44,
                trigger_messages=[{"text": "hi"}],
            )
            self.assertEqual(did, 1)
        row = store.decisions[1]
        self.assertEqual(row.decision, obs.DECISION_SKIPPED)
        self.assertEqual(row.reason, obs.REASON_ESC_OFF)
        self.assertEqual(row.telegram_user_id, 7)
        self.assertEqual(row.trigger_messages[0]["text"], "hi")

    def test_record_decision_never_raises(self):
        with patch.object(obs, "get_db", side_effect=RuntimeError("db down")):
            self.assertIsNone(
                obs.record_escalation_decision(
                    decision=obs.DECISION_FIRED,
                    reason=obs.REASON_PLAYER_IDLE_OPENED,
                    telegram_chat_id=-1,
                )
            )


class NotifyPersistTests(unittest.IsolatedAsyncioTestCase):
    async def test_persists_when_slack_fails(self):
        recorded = {}

        def _record(**kwargs):
            recorded.update(kwargs)
            return 9

        with (
            patch.object(esc, "_club_display_name", return_value="RT"),
            patch(
                "bot.services.escalation_observability.record_escalation_event",
                side_effect=_record,
            ),
            patch(
                "bot.services.escalation_observability.live_history_episode_id",
                return_value=None,
            ),
            patch(
                "bot.services.escalation_observability.update_escalation_event_slack_ok",
            ) as upd,
            patch(
                "bot.services.slack_ops_notify.notify_slack_escalation",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "bot.services.head_admin_escalation.maybe_notify_head_admin_escalation",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            ok, event_id = await esc.notify_escalation_slack(
                esc.REASON_PLAYER_IDLE,
                club_id=1,
                chat_id=-5,
                title="GC",
                message_text="hi",
                trigger_messages=[{"text": "hi", "telegram_message_id": 1}],
            )
        self.assertFalse(ok)
        self.assertEqual(event_id, 9)
        self.assertEqual(recorded["reason"], esc.REASON_PLAYER_IDLE)
        self.assertEqual(recorded["slack_ok"], False)
        upd.assert_called_once_with(9, False)

    async def test_insert_failure_still_slacks(self):
        with (
            patch.object(esc, "_club_display_name", return_value="RT"),
            patch(
                "bot.services.escalation_observability.record_escalation_event",
                return_value=None,
            ),
            patch(
                "bot.services.escalation_observability.live_history_episode_id",
                return_value=None,
            ),
            patch(
                "bot.services.escalation_observability.update_escalation_event_slack_ok",
            ),
            patch(
                "bot.services.slack_ops_notify.notify_slack_escalation",
                new_callable=AsyncMock,
                return_value=True,
            ) as slack,
            patch(
                "bot.services.head_admin_escalation.maybe_notify_head_admin_escalation",
                new_callable=AsyncMock,
            ),
        ):
            ok, event_id = await esc.notify_escalation_slack(
                esc.REASON_CASHOUT_STARTED,
                club_id=1,
                chat_id=-5,
                title="GC",
            )
        self.assertTrue(ok)
        self.assertIsNone(event_id)
        slack.assert_awaited_once()


class IdleEpisodeLinkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from tests.test_support_group_idle_episode import (
            _FakeDbCtx,
            _FakeSession,
            _job_queue,
        )

        _FakeSession.store = {}
        self.jq = _job_queue()
        self.db_patch = patch.object(ep, "get_db", side_effect=_FakeDbCtx)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.close_hist = patch.object(ep, "close_history_episode")
        self.close_hist.start()
        self.addCleanup(self.close_hist.stop)
        for target, value in (
            ("awaiting_agent_debounce_seconds", 60),
            ("idle_episode_hard_cap_seconds", 1800),
            ("idle_episode_silence_seconds", 300),
        ):
            p = patch.object(ep, target, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    async def test_open_passes_episode_id_and_trigger(self):
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        trigger = {
            "telegram_message_id": 9,
            "text": "hello",
            "telegram_user_id": 1,
        }
        with patch.object(
            ep, "notify_escalation_slack", new_callable=AsyncMock, return_value=(True, 1)
        ) as slack:
            with patch.object(
                ep, "offer_idle_help_prompt", new_callable=AsyncMock, return_value=False
            ):
                result = await ep.on_player_reach_out(
                    1,
                    club_id=9,
                    title="GC",
                    message_text="hello",
                    job_queue=self.jq,
                    now=t0,
                    trigger_message=trigger,
                )
        self.assertEqual(result.outcome, "opened")
        slack.assert_awaited_once()
        kwargs = slack.await_args.kwargs
        self.assertEqual(kwargs["trigger_messages"][0]["telegram_message_id"], 9)
        self.assertIsNotNone(kwargs["episode_id"])
        state = ep.load_episode_state(1)
        self.assertEqual(state["history_episode_id"], kwargs["episode_id"])
