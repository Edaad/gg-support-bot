"""Tests for Stripe checkout.session.completed webhook recording."""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from api.routes import stripe_deposit as stripe_routes
from bot.services import stripe_deposit as sd
from db.models import StripeCheckoutSession

SESSION_ID = "cs_test_123"
CHAT_ID = -1001234567890
CLUB_ID = 2


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
    def __init__(self):
        self.sessions: dict[str, StripeCheckoutSession] = {}

    def query(self, model):
        if model is StripeCheckoutSession:
            return FakeCheckoutQuery(self)
        raise AssertionError(f"unexpected model {model}")

    def add(self, obj):
        if isinstance(obj, StripeCheckoutSession):
            if obj.stripe_checkout_session_id in self.sessions:
                raise IntegrityError("", {}, Exception("duplicate"))
            self.sessions[obj.stripe_checkout_session_id] = obj

    def flush(self):
        pass


def _completed_checkout_payload() -> dict:
    return {
        "id": SESSION_ID,
        "customer": "cus_test",
        "amount_total": 5000,
        "currency": "usd",
        "payment_intent": {"id": "pi_test"},
        "client_reference_id": str(CHAT_ID),
        "metadata": {
            "telegram_chat_id": str(CHAT_ID),
            "club_id": str(CLUB_ID),
            "payment_method_id": "7",
        },
    }


class StripeWebhookHandlerTestCase(unittest.TestCase):
    @contextmanager
    def _db(self, store: FakeCheckoutStore):
        @contextmanager
        def fake_get_db():
            yield store

        with patch.object(sd, "get_db", fake_get_db):
            yield

    def test_completed_inserts_payment_row(self):
        store = FakeCheckoutStore()
        event = {
            "type": "checkout.session.completed",
            "data": {"object": _completed_checkout_payload()},
        }
        with self._db(store):
            updated = sd.apply_checkout_session_webhook_event(event)
        self.assertTrue(updated)
        row = store.sessions[SESSION_ID]
        self.assertEqual(row.status, "complete")
        self.assertEqual(row.amount_cents, 5000)
        self.assertEqual(row.telegram_chat_id, CHAT_ID)
        self.assertEqual(row.payment_method_id, 7)
        self.assertEqual(row.stripe_payment_intent_id, "pi_test")

    def test_expired_event_ignored(self):
        store = FakeCheckoutStore()
        event = {
            "type": "checkout.session.expired",
            "data": {"object": {"id": SESSION_ID}},
        }
        with self._db(store):
            updated = sd.apply_checkout_session_webhook_event(event)
        self.assertFalse(updated)
        self.assertEqual(len(store.sessions), 0)

    def test_idempotent_when_already_complete(self):
        store = FakeCheckoutStore()
        payload = _completed_checkout_payload()
        with self._db(store):
            self.assertTrue(sd.record_completed_checkout_payment(payload))
            self.assertFalse(sd.record_completed_checkout_payment(payload))


class StripeNotificationFormatTestCase(unittest.TestCase):
    def test_format_stripe_method_label(self):
        self.assertEqual(sd.format_stripe_method_label("Cashapp"), "Stripe Cashapp")
        self.assertEqual(sd.format_stripe_method_label("Debitcard"), "Stripe Debitcard")
        self.assertEqual(sd.format_stripe_method_label(None), "Stripe")

    def test_format_notification_text(self):
        text = sd.format_stripe_payment_notification_text(
            club_name="Creator Club",
            group_title="CC / 8948-5707 / Alex Wilsoj",
            amount_cents=5000,
            method_label="Stripe Cashapp",
            telegram_chat_id=CHAT_ID,
        )
        self.assertIn("🔔 Creator Club Payment Notification", text)
        self.assertIn("Group Chat: CC / 8948-5707 / Alex Wilsoj", text)
        self.assertNotIn("<a href=", text)
        self.assertIn("Amount: <b>$50</b>", text)
        self.assertIn("Method: Stripe Cashapp", text)
        self.assertNotIn("Open group chat", text)
        self.assertNotIn("Player ID", text)
        self.assertNotIn("Group:", text)


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
        checkout_obj = _completed_checkout_payload()
        event = {
            "type": "checkout.session.completed",
            "data": {"object": checkout_obj},
        }
        with (
            patch.object(stripe_routes, "construct_stripe_webhook_event", return_value=event),
            patch.object(stripe_routes, "apply_checkout_session_webhook_event", return_value=True) as apply_mock,
            patch.object(
                stripe_routes,
                "notify_stripe_payment_completed",
                new=AsyncMock(),
            ) as notify_mock,
        ):
            response = self.client.post(
                "/api/stripe/webhook",
                content=b"{}",
                headers={"Stripe-Signature": "valid_sig"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"received": True})
        apply_mock.assert_called_once_with(event)
        notify_mock.assert_awaited_once_with(checkout_obj)


if __name__ == "__main__":
    unittest.main()
