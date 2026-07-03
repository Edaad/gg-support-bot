"""API tests for aon-beta early-rakeback webhook."""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.early_rakeback_sync import EarlyRakebackSyncReport, EarlyRakebackClubSyncResult
from api.routes.early_rakeback_webhook import router
from db.connection import get_db_dependency


class EarlyRakebackWebhookApiTestCase(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(
            os.environ,
            {"AON_BETA_INTERNAL_API_KEY": "test-internal-key"},
            clear=False,
        )
        self.env_patch.start()
        self.mock_db = MagicMock()
        self.mock_db.commit = MagicMock()
        self.mock_db.rollback = MagicMock()

        app = FastAPI()
        app.include_router(router)

        mock_db = self.mock_db

        def override_db():
            yield mock_db

        app.dependency_overrides[get_db_dependency] = override_db
        self.client = TestClient(app)

    def tearDown(self):
        self.env_patch.stop()

    @patch("api.routes.early_rakeback_webhook.trigger_early_rakeback_sync_for_occurred_at")
    def test_webhook_triggers_sync(self, mock_trigger):
        mock_trigger.return_value = EarlyRakebackSyncReport(
            audit_date=date(2026, 7, 3),
            clubs_synced=1,
            total_lines_stored=2,
            clubs=[
                EarlyRakebackClubSyncResult(
                    club_slug="round-table",
                    club_name="Round Table",
                    snapshot_id=9,
                    lines_stored=2,
                )
            ],
        )

        res = self.client.post(
            "/api/audit/early-rakeback/webhook",
            headers={"X-Internal-Api-Key": "test-internal-key"},
            json={
                "club_slug": "round-table",
                "occurred_at": "2026-07-03T20:58:09Z",
            },
        )

        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["audit_date"], "2026-07-03")
        self.assertEqual(body["total_lines_stored"], 2)
        mock_trigger.assert_called_once()
        args, kwargs = mock_trigger.call_args
        self.assertEqual(args[1], "round-table")
        self.assertEqual(kwargs["occurred_at"], datetime(2026, 7, 3, 20, 58, 9, tzinfo=timezone.utc))
        self.mock_db.commit.assert_called_once()

    def test_webhook_rejects_bad_key(self):
        res = self.client.post(
            "/api/audit/early-rakeback/webhook",
            headers={"X-Internal-Api-Key": "wrong"},
            json={"club_slug": "round-table"},
        )
        self.assertEqual(res.status_code, 401)

    def test_webhook_rejects_unknown_club(self):
        res = self.client.post(
            "/api/audit/early-rakeback/webhook",
            headers={"X-Internal-Api-Key": "test-internal-key"},
            json={"club_slug": "not-a-club"},
        )
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
