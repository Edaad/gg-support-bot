"""Tests for deposit funnel event recording and analytics API."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import create_token, get_current_admin
from api.payments_helpers import is_analytics_excluded_group_title
from api.routes.deposit_funnel import router
from bot.services.deposit_funnel_events import (
    STEP_CHIPS_CREDITED,
    STEP_DEPOSIT_STARTED,
    STEP_INSTRUCTIONS_SENT,
    STEP_PAYMENT_BOUND,
    STEP_PAYMENT_RECEIVED,
    new_deposit_session_id,
    record_deposit_funnel_event,
    record_payment_funnel_from_ingest,
    resolve_deposit_session_for_chat,
)
from db.connection import get_db_dependency
from db.models import DepositFunnelEvent

TOKEN = create_token()


def _make_app(mock_db: MagicMock | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    db = mock_db or MagicMock()

    def override_admin():
        return "admin"

    def override_db():
        yield db

    app.dependency_overrides[get_current_admin] = override_admin
    app.dependency_overrides[get_db_dependency] = override_db
    return app


class DepositFunnelEventsServiceTest(unittest.TestCase):
    def test_new_session_id_is_uuid_string(self):
        session_id = new_deposit_session_id()
        self.assertIsInstance(session_id, str)
        self.assertGreater(len(session_id), 20)

    @patch("bot.services.deposit_funnel_events.get_db")
    def test_record_event_idempotent(self, mock_get_db):
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        session.query.return_value.filter_by.return_value.one_or_none.return_value = None
        session_id = new_deposit_session_id()
        record_deposit_funnel_event(
            deposit_session_id=session_id,
            step=STEP_DEPOSIT_STARTED,
            telegram_chat_id=-100123,
            club_id=1,
            telegram_user_id=42,
            is_first_deposit=True,
        )
        session.query.return_value.filter_by.return_value.one_or_none.return_value = (
            MagicMock()
        )
        record_deposit_funnel_event(
            deposit_session_id=session_id,
            step=STEP_DEPOSIT_STARTED,
            telegram_chat_id=-100123,
            club_id=1,
            telegram_user_id=42,
            is_first_deposit=True,
        )
        self.assertEqual(session.add.call_count, 1)

    @patch("bot.services.deposit_funnel_events.get_db")
    def test_resolve_session_prefers_instructions_sent(self, mock_get_db):
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        anchor = DepositFunnelEvent(
            deposit_session_id="sess-1",
            step=STEP_INSTRUCTIONS_SENT,
            telegram_chat_id=-1001,
            club_id=1,
            is_first_deposit=False,
            requires_method_setup=False,
        )
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            anchor
        )
        resolved = resolve_deposit_session_for_chat(-1001)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.deposit_session_id, "sess-1")

    @patch("bot.services.deposit_funnel_events.resolve_deposit_session_for_chat")
    @patch("bot.services.deposit_funnel_events.record_deposit_funnel_event")
    def test_payment_ingest_records_bound_when_auto_bound(
        self, mock_record, mock_resolve
    ):
        mock_resolve.return_value = DepositFunnelEvent(
            deposit_session_id="sess-2",
            step=STEP_INSTRUCTIONS_SENT,
            telegram_chat_id=-1002,
            club_id=2,
            telegram_user_id=7,
            is_first_deposit=True,
            requires_method_setup=False,
        )
        record_payment_funnel_from_ingest(
            telegram_chat_id=-1002,
            club_id=2,
            amount_cents=5000,
            payment_method_slug="venmo",
            payment_id=99,
            auto_bound=True,
        )
        steps = [call.kwargs.get("step") for call in mock_record.call_args_list]
        self.assertIn(STEP_PAYMENT_RECEIVED, steps)
        self.assertIn(STEP_PAYMENT_BOUND, steps)

    @patch("bot.services.deposit_funnel_events.resolve_deposit_session_for_chat")
    @patch("bot.services.deposit_funnel_events.record_deposit_funnel_event")
    def test_payment_ingest_skips_without_anchor(self, mock_record, mock_resolve):
        mock_resolve.return_value = None
        record_payment_funnel_from_ingest(
            telegram_chat_id=-1002,
            club_id=2,
            amount_cents=5000,
            payment_method_slug="venmo",
            payment_id=99,
            auto_bound=True,
        )
        mock_record.assert_not_called()


class DepositFunnelApiTest(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(
            os.environ, {"DASHBOARD_PASSWORD": "changeme"}, clear=False
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()

    def test_summary_requires_auth(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        response = client.get("/api/deposits/funnel/summary")
        self.assertIn(response.status_code, (401, 403))

    def test_summary_returns_steps(self):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.with_entities.return_value.group_by.return_value.all.return_value = [
            ("deposit_started", 10),
            ("instructions_sent", 8),
            ("chips_credited", 5),
        ]
        client = TestClient(_make_app(mock_db))
        response = client.get(
            "/api/deposits/funnel/summary",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["started"], 10)
        step_ids = [row["step"] for row in data["steps"]]
        self.assertIn("deposit_started", step_ids)
        self.assertIn("chips_credited", step_ids)
        started_row = next(row for row in data["steps"] if row["step"] == "deposit_started")
        chips_row = next(row for row in data["steps"] if row["step"] == "chips_credited")
        self.assertEqual(started_row["count"], 10)
        self.assertEqual(chips_row["count"], 5)
        self.assertAlmostEqual(chips_row["conversion_rate"], 0.5)

    def test_events_list_pagination(self):
        mock_db = MagicMock()
        created = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        row = DepositFunnelEvent(
            id=1,
            deposit_session_id="sess-x",
            step=STEP_CHIPS_CREDITED,
            club_id=1,
            telegram_user_id=9,
            telegram_chat_id=-1009,
            method_slug="venmo",
            amount_cents=2500,
            is_first_deposit=False,
            requires_method_setup=False,
            metadata_json={"path": "e2e_auto_deposit"},
            created_at=created,
        )
        events_q = MagicMock()
        events_q.filter.return_value = events_q
        events_q.count.return_value = 1
        events_q.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            row
        ]
        club = MagicMock()
        club.name = "ClubGTO"
        mock_db.query.side_effect = lambda model: (
            events_q if model is DepositFunnelEvent else MagicMock(
                filter=MagicMock(
                    return_value=MagicMock(first=MagicMock(return_value=club))
                )
            )
        )
        client = TestClient(_make_app(mock_db))
        response = client.get(
            "/api/deposits/funnel/events?step=chips_credited&limit=10",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["step"], STEP_CHIPS_CREDITED)


class DepositFunnelAnalyticsExclusionTest(unittest.TestCase):
    def test_staging_group_title_excluded(self):
        self.assertTrue(is_analytics_excluded_group_title("CC / 8834-2222/ @jz034"))

    def test_normal_group_title_included(self):
        self.assertFalse(
            is_analytics_excluded_group_title("RT / 1234-5678 / Player Name")
        )


if __name__ == "__main__":
    unittest.main()
