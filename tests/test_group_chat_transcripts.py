"""Tests for nightly group-chat transcript extraction and REST reads."""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import create_token, get_current_admin
from api.routes.group_chat_activity import router
from bot.services import group_chat_transcript_cron as cron
from bot.services import group_chat_transcript_fetch as fetch
from bot.services.mtproto_club_health import STATUS_DISCONNECTED
from db.connection import get_db_dependency

TOKEN = create_token()


class DayWindowTest(unittest.TestCase):
    def test_et_day_window_utc_bounds(self):
        # 2026-07-16 America/New_York (EDT, UTC-4) → [04:00 UTC Jul 16, 04:00 UTC Jul 17)
        start, end = fetch.et_day_window_utc(date(2026, 7, 16))
        self.assertEqual(start, datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 7, 17, 4, 0, tzinfo=timezone.utc))

    def test_previous_et_activity_date_is_t_plus_one(self):
        # 2026-07-17 07:00 UTC = 2026-07-17 03:00 EDT → previous day 2026-07-16
        now = datetime(2026, 7, 17, 7, 0, tzinfo=timezone.utc)
        self.assertEqual(fetch.previous_et_activity_date(now), date(2026, 7, 16))


class SerializeMessageTest(unittest.TestCase):
    def test_serialize_human_message(self):
        sender = SimpleNamespace(
            first_name="Ada",
            last_name="Lovelace",
            title=None,
            username="ada",
            bot=False,
        )
        msg = SimpleNamespace(
            id=42,
            date=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
            sender_id=111,
            sender=sender,
            message="hello",
            text="hello",
            reply_to=SimpleNamespace(reply_to_msg_id=9),
            media=None,
            edit_date=None,
            action=None,
        )
        data = fetch.serialize_telethon_message(msg)
        self.assertEqual(data["id"], 42)
        self.assertEqual(data["sender_id"], 111)
        self.assertEqual(data["sender_name"], "Ada Lovelace")
        self.assertEqual(data["username"], "ada")
        self.assertFalse(data["is_bot"])
        self.assertEqual(data["text"], "hello")
        self.assertEqual(data["reply_to_msg_id"], 9)
        self.assertIsNone(data["media_type"])
        self.assertFalse(data["is_service"])

    def test_serialize_service_message(self):
        from telethon.tl.types import MessageActionChatAddUser

        action = MessageActionChatAddUser(users=[1])
        msg = SimpleNamespace(
            id=7,
            date=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
            sender_id=None,
            sender=None,
            message=None,
            text=None,
            reply_to=None,
            media=None,
            edit_date=None,
            action=action,
        )
        # isinstance(msg, MessageService) is False for SimpleNamespace; action path still sets text
        data = fetch.serialize_telethon_message(msg)
        self.assertTrue(data["is_service"])
        self.assertEqual(data["text"], "MessageActionChatAddUser")


class CronPauseResumeTest(unittest.IsolatedAsyncioTestCase):
    async def test_pauses_resumes_and_notifies_slack(self):
        summary = fetch.TranscriptRunSummary(
            activity_date=date(2026, 7, 16),
            complete=2,
            failed=1,
            timed_out=0,
        )
        with (
            patch.object(
                cron,
                "notify_slack_issue_report",
                new_callable=AsyncMock,
                return_value=(True, "1.0", []),
            ) as slack,
            patch.object(cron, "fetch_with_retries", new_callable=AsyncMock) as fetch_mock,
            patch(
                "bot.services.mtproto_dm_gc_listener.set_planned_mtproto_pause"
            ) as set_pause,
            patch(
                "bot.services.mtproto_dm_gc_listener.stop_listener_background"
            ) as stop,
            patch(
                "bot.services.mtproto_dm_gc_listener.start_listener_background"
            ) as start,
            patch.object(
                cron,
                "previous_et_activity_date",
                return_value=date(2026, 7, 16),
            ),
        ):
            fetch_mock.return_value = summary
            result = await cron.run_group_chat_transcript_extraction(
                bot_token="tok-123",
                budget_seconds=60,
            )

        self.assertEqual(result["complete"], 2)
        self.assertEqual(result["failed"], 1)
        stop.assert_called_once()
        start.assert_called_once_with("tok-123")
        self.assertEqual(set_pause.call_args_list[0].args[0], True)
        self.assertEqual(set_pause.call_args_list[-1].args[0], False)
        self.assertEqual(slack.await_count, 2)
        start_text = slack.await_args_list[0].args[0]
        done_text = slack.await_args_list[1].args[0]
        self.assertIn("/add", start_text)
        self.assertIn("/cash", start_text)
        self.assertIn("automatic /gc", start_text)
        self.assertIn("available again", done_text)
        self.assertEqual(
            slack.await_args_list[0].kwargs.get("tags"),
            ["account_managers"],
        )

    async def test_resumes_listener_even_when_fetch_raises(self):
        with (
            patch.object(
                cron,
                "notify_slack_issue_report",
                new_callable=AsyncMock,
                return_value=(True, "1.0", []),
            ),
            patch.object(
                cron,
                "fetch_with_retries",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener.set_planned_mtproto_pause"
            ),
            patch("bot.services.mtproto_dm_gc_listener.stop_listener_background"),
            patch(
                "bot.services.mtproto_dm_gc_listener.start_listener_background"
            ) as start,
            patch.object(
                cron,
                "previous_et_activity_date",
                return_value=date(2026, 7, 16),
            ),
        ):
            await cron.run_group_chat_transcript_extraction(bot_token="tok-123")

        start.assert_called_once_with("tok-123")


class PlannedPauseSuppressTest(unittest.IsolatedAsyncioTestCase):
    async def test_planned_pause_skips_disconnect_dm(self):
        from bot.services.mtproto_dm_gc_listener import (
            _report_club_health,
            set_planned_mtproto_pause,
        )

        set_planned_mtproto_pause(True)
        try:
            with (
                patch(
                    "bot.services.mtproto_dm_gc_listener.persist_club_health",
                ),
                patch(
                    "bot.services.mtproto_dm_gc_listener.notify_club_gc_mtproto_disconnected",
                    new_callable=AsyncMock,
                ) as mock_notify,
                patch(
                    "bot.services.mtproto_dm_gc_listener.CLUB_GC_CONFIG",
                    {"round_table": MagicMock(club_key="round_table")},
                ),
            ):
                await _report_club_health(
                    "round_table",
                    worker_connected=False,
                    session_valid=False,
                    status=STATUS_DISCONNECTED,
                    status_detail="planned pause",
                )
            mock_notify.assert_not_awaited()
        finally:
            set_planned_mtproto_pause(False)


def _make_api_app(db: MagicMock) -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    def override_admin():
        return "admin"

    def override_db():
        yield db

    app.dependency_overrides[get_current_admin] = override_admin
    app.dependency_overrides[get_db_dependency] = override_db
    return app


class GroupChatActivityApiTest(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(
            os.environ, {"DASHBOARD_PASSWORD": "changeme"}, clear=False
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()

    def test_activity_requires_auth(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        response = client.get(
            "/api/group-chat-daily-activity",
            params={"activity_date": "2026-07-16"},
        )
        self.assertIn(response.status_code, (401, 403))

    def test_list_activity(self):
        row = SimpleNamespace(
            id=1,
            activity_date=date(2026, 7, 16),
            chat_id=-1001,
            club_id=2,
            non_bot_message_count=3,
            first_message_at=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 7, 16, 13, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 16, 13, 0, tzinfo=timezone.utc),
        )
        db = MagicMock()
        q = db.query.return_value
        q.filter.return_value = q
        q.order_by.return_value = q
        q.all.return_value = [row]

        client = TestClient(_make_api_app(db))
        response = client.get(
            "/api/group-chat-daily-activity",
            params={"activity_date": "2026-07-16"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["chat_id"], -1001)
        self.assertEqual(data[0]["non_bot_message_count"], 3)
        self.assertNotIn("messages", data[0])

    def test_list_transcripts_metadata_only(self):
        row = SimpleNamespace(
            id=9,
            activity_date=date(2026, 7, 16),
            chat_id=-1001,
            club_id=2,
            status="complete",
            message_count=2,
            error=None,
            attempt_count=1,
            fetched_at=datetime(2026, 7, 17, 7, 5, tzinfo=timezone.utc),
            created_at=datetime(2026, 7, 17, 7, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 17, 7, 5, tzinfo=timezone.utc),
            messages=[{"id": 1, "text": "secret"}],
        )
        db = MagicMock()
        q = db.query.return_value
        q.filter.return_value = q
        q.order_by.return_value = q
        q.all.return_value = [row]

        client = TestClient(_make_api_app(db))
        response = client.get(
            "/api/group-chat-transcripts",
            params={"activity_date": "2026-07-16"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["status"], "complete")
        self.assertNotIn("messages", data[0])

    def test_transcript_detail_includes_messages(self):
        row = SimpleNamespace(
            id=9,
            activity_date=date(2026, 7, 16),
            chat_id=-1001,
            club_id=2,
            status="complete",
            message_count=1,
            error=None,
            attempt_count=1,
            fetched_at=datetime(2026, 7, 17, 7, 5, tzinfo=timezone.utc),
            created_at=datetime(2026, 7, 17, 7, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 17, 7, 5, tzinfo=timezone.utc),
            messages=[{"id": 1, "text": "hi"}],
        )
        db = MagicMock()
        q = db.query.return_value
        q.filter.return_value = q
        q.one_or_none.return_value = row

        client = TestClient(_make_api_app(db))
        response = client.get(
            "/api/group-chat-transcripts/-1001",
            params={"activity_date": "2026-07-16"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["messages"], [{"id": 1, "text": "hi"}])

    def test_invalid_activity_date(self):
        client = TestClient(_make_api_app(MagicMock()))
        response = client.get(
            "/api/group-chat-daily-activity",
            params={"activity_date": "not-a-date"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
