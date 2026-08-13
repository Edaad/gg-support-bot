"""Tests for watched non-support group escalation."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import watched_group_escalation as wge


class _FakeRow:
    def __init__(self, chat_id: int):
        self.telegram_chat_id = chat_id
        self.title = None
        self.episode_started_at = None
        self.last_message_at = None
        self.escalated_at = None
        self.burst_json = []
        self.updated_at = None


class _FakeSession:
    store: dict[int, _FakeRow] = {}

    def get(self, _model, chat_id):
        return self.store.get(int(chat_id))

    def add(self, row):
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


class AllowlistTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop(wge.WATCH_GROUP_ESCALATION_CHAT_IDS_ENV, None)

    def test_parse_allowlist(self):
        os.environ[wge.WATCH_GROUP_ESCALATION_CHAT_IDS_ENV] = " -1001, 42, bad, "
        ids = wge.watched_escalation_chat_ids()
        self.assertEqual(ids, frozenset({-1001, 42}))

    def test_empty_allowlist(self):
        os.environ.pop(wge.WATCH_GROUP_ESCALATION_CHAT_IDS_ENV, None)
        self.assertEqual(wge.watched_escalation_chat_ids(), frozenset())

    @patch.object(wge, "fetch_support_group_chat_by_telegram_chat_id", return_value=object())
    def test_support_gc_skipped(self, _fetch):
        os.environ[wge.WATCH_GROUP_ESCALATION_CHAT_IDS_ENV] = "99"
        self.assertTrue(wge.is_env_allowlisted_chat(99))
        self.assertFalse(wge.is_watched_escalation_chat(99))

    @patch.object(wge, "fetch_support_group_chat_by_telegram_chat_id", return_value=None)
    def test_watched_when_allowlisted_non_support(self, _fetch):
        os.environ[wge.WATCH_GROUP_ESCALATION_CHAT_IDS_ENV] = "99"
        self.assertTrue(wge.is_watched_escalation_chat(99))


class IgnoreSenderTests(unittest.TestCase):
    def test_union_automation_accounts(self):
        for username in ("rtaccountant", "widget_stick", "@RTAccountant", "Widget_Stick"):
            user = SimpleNamespace(username=username, is_bot=False)
            self.assertTrue(wge.is_ignored_watched_sender(user), username)

    def test_normal_user_not_ignored(self):
        user = SimpleNamespace(username="ada", is_bot=False)
        self.assertFalse(wge.is_ignored_watched_sender(user))

    def test_missing_username_not_ignored(self):
        user = SimpleNamespace(username=None, is_bot=False)
        self.assertFalse(wge.is_ignored_watched_sender(user))


class FormatTests(unittest.TestCase):
    def test_format_sender_with_username(self):
        user = SimpleNamespace(full_name="Ada Lovelace", first_name="Ada", username="ada")
        self.assertEqual(wge.format_sender_label(user), "Ada Lovelace (@ada)")

    def test_format_sender_without_username(self):
        user = SimpleNamespace(full_name="Ada", first_name="Ada", username=None)
        self.assertEqual(wge.format_sender_label(user), "Ada")

    def test_extract_text(self):
        msg = SimpleNamespace(text=" hello ", caption=None, photo=None, video=None)
        self.assertEqual(wge.extract_watched_message_text(msg), "hello")

    def test_extract_photo_with_caption(self):
        msg = SimpleNamespace(
            text=None,
            caption="  proof  ",
            photo=[object()],
            video=None,
            video_note=None,
            voice=None,
            audio=None,
            document=None,
            animation=None,
            sticker=None,
        )
        self.assertEqual(wge.extract_watched_message_text(msg), "[photo] proof")

    def test_slack_format(self):
        text = wge.format_watched_group_slack_text(
            title="Ops Chat",
            burst=[
                {"from": "Ada (@ada)", "text": "hi"},
                {"from": "Bob", "text": "there"},
            ],
        )
        self.assertEqual(
            text,
            "Watched group activity.\n"
            "Group: Ops Chat\n"
            "From: Ada (@ada)\n"
            "hi\n"
            "---\n"
            "From: Bob\n"
            "there",
        )


class EpisodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _FakeSession.store = {}
        self._db_patch = patch.object(wge, "get_db", _FakeDbCtx)
        self._db_patch.start()
        self._support_patch = patch.object(
            wge, "fetch_support_group_chat_by_telegram_chat_id", return_value=None
        )
        self._support_patch.start()

    def tearDown(self):
        self._db_patch.stop()
        self._support_patch.stop()
        _FakeSession.store = {}

    def test_start_schedules_episode_and_debounce(self):
        jq = _job_queue()
        ok = wge.on_watched_group_message(
            99,
            title="Ops",
            sender_label="Ada (@ada)",
            message_text="need eyes",
            job_queue=jq,
        )
        self.assertTrue(ok)
        self.assertEqual(jq.run_once.call_count, 2)
        callbacks = [c.args[0] for c in jq.run_once.call_args_list]
        self.assertIn(wge._watched_group_debounce_callback, callbacks)
        self.assertIn(wge._watched_group_episode_end_callback, callbacks)
        delays = {c.kwargs["when"] for c in jq.run_once.call_args_list}
        self.assertEqual(
            delays,
            {
                float(wge.awaiting_agent_debounce_seconds()),
                float(wge.awaiting_agent_episode_seconds()),
            },
        )
        state = wge.load_episode_state(99)
        self.assertIsNotNone(state)
        self.assertEqual(len(state["burst"]), 1)
        self.assertEqual(state["burst"][0]["text"], "need eyes")

    def test_second_message_restarts_debounce_and_accumulates(self):
        jq = _job_queue()
        wge.on_watched_group_message(
            99,
            title="Ops",
            sender_label="Ada (@ada)",
            message_text="first",
            job_queue=jq,
        )
        jq.run_once.reset_mock()
        jq.get_jobs_by_name.return_value = []
        ok = wge.on_watched_group_message(
            99,
            title="Ops",
            sender_label="Bob",
            message_text="second",
            job_queue=jq,
        )
        self.assertTrue(ok)
        jq.run_once.assert_called_once()
        self.assertIs(
            jq.run_once.call_args.args[0],
            wge._watched_group_debounce_callback,
        )
        state = wge.load_episode_state(99)
        self.assertEqual([b["text"] for b in state["burst"]], ["first", "second"])

    async def test_debounce_fires_once(self):
        jq = _job_queue()
        now = datetime.now(timezone.utc)
        wge.on_watched_group_message(
            99,
            title="Ops",
            sender_label="Ada (@ada)",
            message_text="hello",
            job_queue=jq,
            now=now,
        )
        # Pretend quiet period already elapsed.
        row = _FakeSession.store[99]
        row.last_message_at = now - timedelta(seconds=120)

        context = SimpleNamespace(
            job=SimpleNamespace(data={"chat_id": 99}, chat_id=99),
            job_queue=jq,
        )
        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new_callable=AsyncMock,
        ) as notify:
            await wge._watched_group_debounce_callback(context)
            notify.assert_awaited_once()
            posted = notify.await_args.args[0]
            self.assertIn("Watched group activity.", posted)
            self.assertIn("Group: Ops", posted)
            self.assertIn("From: Ada (@ada)", posted)
            self.assertIn("hello", posted)
            self.assertNotIn("chat_id=", posted.lower())
            self.assertEqual(notify.await_args.kwargs["source"], "watched_group")

        self.assertIsNotNone(_FakeSession.store[99].escalated_at)

        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new_callable=AsyncMock,
        ) as notify2:
            await wge._watched_group_debounce_callback(context)
            notify2.assert_not_awaited()

        # Further messages ignored until episode ends.
        ok = wge.on_watched_group_message(
            99,
            title="Ops",
            sender_label="Ada (@ada)",
            message_text="again",
            job_queue=jq,
        )
        self.assertFalse(ok)

    async def test_episode_end_clears_allows_new(self):
        jq = _job_queue()
        wge.on_watched_group_message(
            99,
            title="Ops",
            sender_label="Ada (@ada)",
            message_text="hello",
            job_queue=jq,
        )
        context = SimpleNamespace(
            job=SimpleNamespace(data={"chat_id": 99}, chat_id=99),
            job_queue=jq,
        )
        await wge._watched_group_episode_end_callback(context)
        self.assertIsNone(wge.load_episode_state(99))

        jq.run_once.reset_mock()
        ok = wge.on_watched_group_message(
            99,
            title="Ops",
            sender_label="Ada (@ada)",
            message_text="fresh",
            job_queue=jq,
        )
        self.assertTrue(ok)
        self.assertEqual(jq.run_once.call_count, 2)

    def test_restore_rearms_jobs(self):
        jq = _job_queue()
        now = datetime.now(timezone.utc)
        row = _FakeRow(77)
        row.title = "Ops"
        row.episode_started_at = now - timedelta(seconds=60)
        row.last_message_at = now - timedelta(seconds=10)
        row.burst_json = [{"from": "Ada", "text": "hi"}]
        _FakeSession.store[77] = row

        wge.restore_watched_group_escalation_jobs(jq)
        self.assertEqual(jq.run_once.call_count, 2)
        callbacks = [c.args[0] for c in jq.run_once.call_args_list]
        self.assertIn(wge._watched_group_debounce_callback, callbacks)
        self.assertIn(wge._watched_group_episode_end_callback, callbacks)


class GateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _FakeSession.store = {}
        self._db_patch = patch.object(wge, "get_db", _FakeDbCtx)
        self._db_patch.start()
        os.environ[wge.WATCH_GROUP_ESCALATION_CHAT_IDS_ENV] = "55"
        self._support_patch = patch.object(
            wge, "fetch_support_group_chat_by_telegram_chat_id", return_value=None
        )
        self._support_patch.start()

    def tearDown(self):
        self._db_patch.stop()
        self._support_patch.stop()
        os.environ.pop(wge.WATCH_GROUP_ESCALATION_CHAT_IDS_ENV, None)
        _FakeSession.store = {}

    async def test_command_feeds_burst_then_stops_without_immediate_slack(self):
        from telegram.ext import ApplicationHandlerStop

        jq = _job_queue()
        update = SimpleNamespace(
            effective_message=SimpleNamespace(
                text="/deposit",
                caption=None,
                photo=None,
                video=None,
                video_note=None,
                voice=None,
                audio=None,
                document=None,
                animation=None,
                sticker=None,
            ),
            effective_chat=SimpleNamespace(id=55, type="supergroup", title="Ops"),
            effective_user=SimpleNamespace(
                is_bot=False,
                full_name="Ada",
                first_name="Ada",
                username="ada",
            ),
        )
        context = SimpleNamespace(job_queue=jq)

        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new_callable=AsyncMock,
        ) as notify:
            with self.assertRaises(ApplicationHandlerStop):
                await wge.watched_group_message_gate(update, context)
            notify.assert_not_awaited()

        state = wge.load_episode_state(55)
        self.assertIsNotNone(state)
        self.assertEqual(state["burst"][0]["text"], "/deposit")

    async def test_ignored_username_does_not_open_episode(self):
        from telegram.ext import ApplicationHandlerStop

        jq = _job_queue()
        update = SimpleNamespace(
            effective_message=SimpleNamespace(
                text="settlement ping",
                caption=None,
                photo=None,
                video=None,
                video_note=None,
                voice=None,
                audio=None,
                document=None,
                animation=None,
                sticker=None,
            ),
            effective_chat=SimpleNamespace(id=55, type="supergroup", title="TMT Union"),
            effective_user=SimpleNamespace(
                is_bot=False,
                full_name="RT Accountant",
                first_name="RT",
                username="rtaccountant",
            ),
        )
        context = SimpleNamespace(job_queue=jq)

        with self.assertRaises(ApplicationHandlerStop):
            await wge.watched_group_message_gate(update, context)

        self.assertIsNone(wge.load_episode_state(55))
        jq.run_once.assert_not_called()


if __name__ == "__main__":
    unittest.main()
