"""API tests for Stripe deposit-context lookup (Zapier)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.stripe_deposit import router
from bot.services.stripe_deposit import StripeDepositContext

LOOKUP_SECRET = "test-lookup-secret"


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


class StripeDepositApiTestCase(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(
            os.environ,
            {"STRIPE_ZAPIER_LOOKUP_SECRET": LOOKUP_SECRET},
            clear=False,
        )
        self.env_patch.start()
        self.client = TestClient(_make_app())

    def tearDown(self):
        self.env_patch.stop()

    def test_deposit_context_unauthorized_without_secret(self):
        response = self.client.get(
            "/api/stripe/deposit-context",
            params={"customer_id": "cus_test"},
        )
        self.assertEqual(response.status_code, 401)

    def test_deposit_context_not_found(self):
        with patch(
            "api.routes.stripe_deposit.lookup_deposit_context_by_customer_id",
            return_value=None,
        ):
            response = self.client.get(
                "/api/stripe/deposit-context",
                params={"customer_id": "cus_missing"},
                headers={"X-Stripe-Lookup-Secret": LOOKUP_SECRET},
            )
        self.assertEqual(response.status_code, 404)

    def test_deposit_context_success(self):
        ctx = StripeDepositContext(
            telegram_chat_id=-1001234567890,
            group_title="RT / 6485-8168 / Angus Mcgoon",
            club_id=2,
            club_name="Round Table",
            gg_player_id="6485-8168",
            player_display_name="Angus Mcgoon",
            stripe_customer_id="cus_test",
        )
        with patch(
            "api.routes.stripe_deposit.lookup_deposit_context_by_customer_id",
            return_value=ctx,
        ):
            response = self.client.get(
                "/api/stripe/deposit-context",
                params={"customer_id": "cus_test"},
                headers={"X-Stripe-Lookup-Secret": LOOKUP_SECRET},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["stripe_customer_id"], "cus_test")
        self.assertEqual(data["group_title"], "RT / 6485-8168 / Angus Mcgoon")
        self.assertEqual(data["gg_player_id"], "6485-8168")
        self.assertEqual(data["club_name"], "Round Table")
        self.assertEqual(data["telegram_chat_id"], -1001234567890)


if __name__ == "__main__":
    unittest.main()
