"""API tests for Cash App payment ingest (Zapier)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.cashapp_payments import router
from bot.services.cashapp_payments import IngestResult, WEBHOOK_SECRET_ENV

WEBHOOK_SECRET = "test-cashapp-webhook-secret"


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


class CashAppPaymentsApiTestCase(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(
            os.environ,
            {WEBHOOK_SECRET_ENV: WEBHOOK_SECRET},
            clear=False,
        )
        self.env_patch.start()
        self.client = TestClient(_make_app())

    def tearDown(self):
        self.env_patch.stop()

    def test_ingest_unauthorized_without_secret(self):
        response = self.client.post(
            "/api/cashapp/payments",
            json={
                "payer_name": "Jackson Taylor",
                "amount": "15.00",
                "cashapp_handle": "$michaelc4444",
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_ingest_success(self):
        with patch(
            "api.routes.cashapp_payments.ingest_cashapp_payment",
            new=AsyncMock(
                return_value=IngestResult(
                    payment_id=42,
                    status="unbound",
                    auto_bound=False,
                    created=True,
                )
            ),
        ):
            response = self.client.post(
                "/api/cashapp/payments",
                json={
                    "payer_name": "Jackson Taylor",
                    "amount": "15.00",
                    "cashapp_handle": "$michaelc4444",
                    "memo": "FLOP",
                },
                headers={"X-Cashapp-Webhook-Secret": WEBHOOK_SECRET},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["payment_id"], 42)
        self.assertEqual(data["status"], "unbound")
        self.assertFalse(data["auto_bound"])
        self.assertTrue(data["created"])
