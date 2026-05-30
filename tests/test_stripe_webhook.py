"""Tests for Stripe checkout session webhook lifecycle updates."""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import stripe_deposit as stripe_routes
from bot.services import stripe_deposit as sd
from db.models import StripeCheckoutSession

SESSION_ID = "cs_test_123"


class FakeCheckoutQuery:
    def __init__(self, store: "FakeCheckoutStore"):
        self._store = store
        self._session_id: str | None = None

    def filter(self, *args, **kwargs):
        for expr in args:
            left = getattr(expr, "left", None)
            right = getattr(expr, "right", None)
            if left is not None and hasattr(left, "key") and left.key == "stripe_checkout_session_id":
                self._session_id = str(right.value if hasattr(right, "value") else right)
        return self

    def one_or_none(self):
        if self._session_id is None:
            return None
        return self._store.sessions.get(self._session_id)


class FakeCheckoutStore:
    def __init__(self, row: StripeCheckoutSession | None = None):
        self.sessions: dict[str, StripeCheckoutSession] = {}
        if row is not None:
            self.sessions[row.stripe_checkout_session_id] = row

    def query(self, model):
        if model is StripeCheckoutSession:
            return FakeCheckoutQuery(self)
        raise AssertionError(f"unexpected model {model}")

    def flush(self):
        pass


def _open_session() -> StripeCheckoutSession:
    return StripeCheckoutSession(
        stripe_checkout_session_id=SESSION_ID,
        stripe_customer_id="cus_test",
        telegram_chat_id=-1001234567890,
        club_id=2,
        amount_cents=0,
        currency="usd",
        status="open",
    )


class StripeWebhookHandlerTestCase(unittest.TestCase):
    @contextmanager
    def _db(self, store: FakeCheckoutStore):
        @contextmanager
        def fake_get_db():
            yield store

        with patch.object(sd, "get_db", fake_get_db):
            yield

    def test_complete_updates_amount_and_status(self):
        store = FakeCheckoutStore(_open_session())
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": SESSION_ID,
                    "amount_total": 5000,
                    "payment_intent": "pi_test",
                }
            },
        }
        with self._db(store):
            updated = sd.apply_checkout_session_webhook_event(event)
        self.assertTrue(updated)
        row = store.sessions[SESSION_ID]
        self.assertEqual(row.status, "complete")
        self.assertEqual(row.amount_cents, 5000)
        self.assertEqual(row.stripe_payment_intent_id, "pi_test")
        self.assertIsNotNone(row.completed_at)

    def test_expired_updates_status(self):
        store = FakeCheckoutStore(_open_session())
        event = {
            "type": "checkout.session.expired",
            "data": {"object": {"id": SESSION_ID}},
        }
        with self._db(store):
            updated = sd.apply_checkout_session_webhook_event(event)
        self.assertTrue(updated)
        self.assertEqual(store.sessions[SESSION_ID].status, "expired")

    def test_idempotent_when_already_complete(self):
        row = _open_session()
        row.status = "complete"
        row.amount_cents = 5000
        store = FakeCheckoutStore(row)
        event = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": SESSION_ID, "amount_total": 9999}},
        }
        with self._db(store):
            updated = sd.apply_checkout_session_webhook_event(event)
        self.assertFalse(updated)
        self.assertEqual(store.sessions[SESSION_ID].amount_cents, 5000)


class StripeWebhookApiTestCase(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(
            os.environ,
            {"STRIPE_WEBHOOK_SECRET": "whsec_test"},
            clear=False,
        )
        self.env_patch.start()
        app = FastAPI()
        app.include_router(stripe_routes.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.env_patch.stop()

    def test_webhook_rejects_missing_secret_config(self):
        with patch.dict(os.environ, {"STRIPE_WEBHOOK_SECRET": ""}, clear=False):
            response = self.client.post(
                "/api/stripe/webhook",
                content=b"{}",
                headers={"Stripe-Signature": "sig"},
            )
        self.assertEqual(response.status_code, 503)

    def test_webhook_accepts_valid_event(self):
        event = {"type": "checkout.session.completed", "data": {"object": {"id": SESSION_ID}}}
        with (
            patch.object(stripe_routes, "construct_stripe_webhook_event", return_value=event),
            patch.object(stripe_routes, "apply_checkout_session_webhook_event", return_value=True) as apply_mock,
        ):
            response = self.client.post(
                "/api/stripe/webhook",
                content=b"{}",
                headers={"Stripe-Signature": "valid_sig"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"received": True})
        apply_mock.assert_called_once_with(event)


if __name__ == "__main__":
    unittest.main()
