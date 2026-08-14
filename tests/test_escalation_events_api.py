"""API tests for escalation observability routes."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import create_token, get_current_admin
from api.routes.escalation_events import router
from db.connection import get_db_dependency
from db.models import EscalationEpisode, EscalationEvent

TOKEN = create_token()


def _make_app(db: MagicMock) -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    def override_admin():
        return {"role": "admin"}

    def override_db():
        yield db

    app.dependency_overrides[get_current_admin] = override_admin
    app.dependency_overrides[get_db_dependency] = override_db
    return app


class EscalationEventsApiTests(unittest.TestCase):
    def test_list_filters_reason(self):
        db = MagicMock()
        q = db.query.return_value
        q.filter.return_value = q
        q.count.return_value = 1
        q.order_by.return_value = q
        q.offset.return_value = q
        q.limit.return_value = q
        row = EscalationEvent(
            id=3,
            reason="player_idle",
            club_id=2,
            telegram_chat_id=-100,
            group_title="GC",
            episode_id=None,
            slack_ok=True,
            head_admin_fanout=False,
            method_slug=None,
            trigger_messages=[{"text": "hi"}],
            created_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        )
        q.all.return_value = [row]
        client = TestClient(_make_app(db))
        resp = client.get(
            "/api/escalations/events?reason=player_idle",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["reason"], "player_idle")
        self.assertEqual(body["items"][0]["trigger_messages"][0]["text"], "hi")

    def test_get_episode_with_events(self):
        db = MagicMock()
        eid = uuid4()
        ep_row = EscalationEpisode(
            id=eid,
            telegram_chat_id=-100,
            club_id=2,
            group_title="GC",
            opened_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
            closed_at=None,
            close_reason=None,
            trigger_messages=[{"text": "hello"}],
        )
        db.get.return_value = ep_row
        q = db.query.return_value
        q.filter.return_value = q
        q.order_by.return_value = q
        ev = EscalationEvent(
            id=1,
            reason="player_idle",
            club_id=2,
            telegram_chat_id=-100,
            group_title="GC",
            episode_id=eid,
            slack_ok=True,
            head_admin_fanout=False,
            trigger_messages=[{"text": "hello"}],
            created_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        )
        q.all.return_value = [ev]
        client = TestClient(_make_app(db))
        resp = client.get(
            f"/api/escalations/episodes/{eid}",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["id"], str(eid))
        self.assertEqual(len(body["events"]), 1)
        self.assertEqual(body["events"][0]["reason"], "player_idle")
