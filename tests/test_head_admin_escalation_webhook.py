"""API tests for head-admin escalation inbound webhook (Make/Zapier)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.head_admin_escalation import WEBHOOK_SECRET_ENV, router

WEBHOOK_SECRET = "test-head-admin-escalation-webhook-secret"
HEADER = "X-Head-Admin-Escalation-Webhook-Secret"


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


class HeadAdminEscalationWebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(
            os.environ,
            {WEBHOOK_SECRET_ENV: WEBHOOK_SECRET},
            clear=False,
        )
        self.env_patch.start()
        self.client = TestClient(_make_app())

    def tearDown(self) -> None:
        self.env_patch.stop()

    def test_unauthorized_without_secret(self) -> None:
        response = self.client.post(
            "/api/head-admin-escalation",
            json={"message": "urgent"},
        )
        self.assertEqual(response.status_code, 401)

    def test_unauthorized_wrong_secret(self) -> None:
        response = self.client.post(
            "/api/head-admin-escalation",
            headers={HEADER: "wrong"},
            json={"message": "urgent"},
        )
        self.assertEqual(response.status_code, 401)

    def test_503_when_secret_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post(
                "/api/head-admin-escalation",
                headers={HEADER: WEBHOOK_SECRET},
                json={"message": "urgent"},
            )
        self.assertEqual(response.status_code, 503)

    def test_422_empty_message(self) -> None:
        response = self.client.post(
            "/api/head-admin-escalation",
            headers={HEADER: WEBHOOK_SECRET},
            json={"message": "   "},
        )
        self.assertEqual(response.status_code, 422)

    def test_posts_message_to_head_admin(self) -> None:
        text = (
            "🚨 URGENT 🚨\n\n"
            "Contact head admins immediately to cash the following player "
            "on the Hub who has been waiting longer than 5 minutes: "
            "GTO / 8226-6281 / Atticus"
        )
        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new_callable=AsyncMock,
            return_value=True,
        ) as notify:
            response = self.client.post(
                "/api/head-admin-escalation",
                headers={HEADER: WEBHOOK_SECRET},
                json={"message": text},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        notify.assert_awaited_once_with(
            text, source="head_admin_escalation_webhook"
        )

    def test_502_when_slack_fails(self) -> None:
        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new_callable=AsyncMock,
            return_value=False,
        ):
            response = self.client.post(
                "/api/head-admin-escalation",
                headers={HEADER: WEBHOOK_SECRET},
                json={"message": "urgent"},
            )
        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
