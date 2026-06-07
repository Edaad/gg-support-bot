"""API tests for crypto payment ingest (Zapier / Arkham)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.crypto_payments import router
from bot.services.crypto_payments import IngestResult, WEBHOOK_SECRET_ENV

WEBHOOK_SECRET = "test-crypto-webhook-secret"

SAMPLE_BODY = {
    "amount": "122.00",
    "token_symbol": "USDC",
    "token_name": "USD Coin",
    "chain": "bsc",
    "from_address": "0x8894E0a0c962CB723c1976a4421c95949bE2D4E3",
    "from_entity_name": "Binance",
    "to_address": "0x7063760294b901CF56b34BEB6275A641B5178CDa",
    "transaction_hash": "0xa64ed1c7ecf9dbd350f2738f9d8f0699625ee957e42a4bd6dc165c619936f6d3",
    "paid_at": "2026-05-06T23:56:53Z",
    "source_external_id": "0xa64ed1c7ecf9dbd350f2738f9d8f0699625ee957e42a4bd6dc165c619936f6d3_59",
    "alert_name": "ClubGTO Crypto Payment",
}


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


class CryptoPaymentsApiTestCase(unittest.TestCase):
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
        response = self.client.post("/api/crypto/payments", json=SAMPLE_BODY)
        self.assertEqual(response.status_code, 401)

    def test_ingest_success(self):
        with patch(
            "api.routes.crypto_payments.ingest_crypto_payment",
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
                "/api/crypto/payments",
                json=SAMPLE_BODY,
                headers={"X-Crypto-Webhook-Secret": WEBHOOK_SECRET},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["payment_id"], 42)
        self.assertEqual(data["status"], "unbound")
        self.assertFalse(data["auto_bound"])
        self.assertTrue(data["created"])


if __name__ == "__main__":
    unittest.main()
