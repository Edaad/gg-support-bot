"""API tests for Venmo payment ingest (Zapier)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.venmo_payments import router
from bot.services.venmo_payments import IngestResult, WEBHOOK_SECRET_ENV

WEBHOOK_SECRET = "test-venmo-webhook-secret"


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


class VenmoPaymentsApiTestCase(unittest.TestCase):
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
            "/api/venmo/payments",
            json={
                "payer_name": "Moshe Toussoun",
                "amount": "200.00",
                "venmo_handle": "@godfather4444",
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_ingest_success(self):
        with patch(
            "api.routes.venmo_payments.ingest_venmo_payment",
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
                "/api/venmo/payments",
                json={
                    "payer_name": "Moshe Toussoun",
                    "amount": "200.00",
                    "venmo_handle": "@godfather4444",
                    "goods_or_services": False,
                },
                headers={"X-Venmo-Webhook-Secret": WEBHOOK_SECRET},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["payment_id"], 42)
        self.assertEqual(data["status"], "unbound")
        self.assertFalse(data["auto_bound"])
        self.assertTrue(data["created"])

    def test_ingest_auto_bound(self):
        with patch(
            "api.routes.venmo_payments.ingest_venmo_payment",
            new=AsyncMock(
                return_value=IngestResult(
                    payment_id=7,
                    status="bound",
                    auto_bound=True,
                    created=True,
                )
            ),
        ):
            response = self.client.post(
                "/api/venmo/payments",
                json={
                    "payer_name": "Moshe Toussoun",
                    "amount": "150.00",
                    "venmo_handle": "@godfather4444",
                },
                headers={"X-Venmo-Webhook-Secret": WEBHOOK_SECRET},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["auto_bound"])


if __name__ == "__main__":
    unittest.main()
