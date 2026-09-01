"""Tests for webhook ingest request audit logging."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.venmo_payments import router
from api.webhook_ingest_audit import (
    OUTCOME_AUTH_FAILED,
    OUTCOME_MISCONFIGURED,
    OUTCOME_PROCESSING_ERROR,
    OUTCOME_REJECTED,
    OUTCOME_STRIPE_IGNORED,
    OUTCOME_STRIPE_PROCESSED,
    OUTCOME_SUCCESS_CREATED,
    OUTCOME_SUCCESS_DUPLICATE,
    OUTCOME_VALIDATION_ERROR,
    WebhookIngestContext,
    WebhookIngestMiddleware,
    derive_outcome,
    parse_request_body,
    record_webhook_ingest_request,
    source_for_path,
)
from bot.services.venmo_payments import WEBHOOK_SECRET_ENV, IngestResult

WEBHOOK_SECRET = "test-venmo-webhook-secret"


class DeriveOutcomeTestCase(unittest.TestCase):
    def test_auth_failed(self):
        outcome = derive_outcome(
            source="venmo",
            http_status_code=401,
            ctx=WebhookIngestContext(),
        )
        self.assertEqual(outcome, OUTCOME_AUTH_FAILED)

    def test_validation_error(self):
        outcome = derive_outcome(
            source="venmo",
            http_status_code=422,
            ctx=WebhookIngestContext(),
        )
        self.assertEqual(outcome, OUTCOME_VALIDATION_ERROR)

    def test_rejected(self):
        outcome = derive_outcome(
            source="venmo",
            http_status_code=400,
            ctx=WebhookIngestContext(error_message="bad amount"),
        )
        self.assertEqual(outcome, OUTCOME_REJECTED)

    def test_misconfigured(self):
        outcome = derive_outcome(
            source="venmo",
            http_status_code=503,
            ctx=WebhookIngestContext(
                error_message="VENMO_ZAPIER_WEBHOOK_SECRET is not configured on the server"
            ),
        )
        self.assertEqual(outcome, OUTCOME_MISCONFIGURED)

    def test_processing_error(self):
        outcome = derive_outcome(
            source="venmo",
            http_status_code=503,
            ctx=WebhookIngestContext(error_message="Telegram notification failed"),
        )
        self.assertEqual(outcome, OUTCOME_PROCESSING_ERROR)

    def test_success_created(self):
        outcome = derive_outcome(
            source="venmo",
            http_status_code=200,
            ctx=WebhookIngestContext(
                response_json={"status": "unbound", "auto_bound": False, "created": True}
            ),
        )
        self.assertEqual(outcome, OUTCOME_SUCCESS_CREATED)

    def test_success_duplicate(self):
        outcome = derive_outcome(
            source="venmo",
            http_status_code=200,
            ctx=WebhookIngestContext(
                response_json={"status": "unbound", "auto_bound": False, "created": False}
            ),
        )
        self.assertEqual(outcome, OUTCOME_SUCCESS_DUPLICATE)

    def test_stripe_processed(self):
        outcome = derive_outcome(
            source="stripe",
            http_status_code=200,
            ctx=WebhookIngestContext(
                stripe_event_type="checkout.session.completed",
                stripe_processed=True,
            ),
        )
        self.assertEqual(outcome, OUTCOME_STRIPE_PROCESSED)

    def test_stripe_ignored(self):
        outcome = derive_outcome(
            source="stripe",
            http_status_code=200,
            ctx=WebhookIngestContext(
                stripe_event_type="customer.created",
                stripe_processed=False,
            ),
        )
        self.assertEqual(outcome, OUTCOME_STRIPE_IGNORED)

    def test_outcome_override(self):
        outcome = derive_outcome(
            source="venmo",
            http_status_code=200,
            ctx=WebhookIngestContext(outcome_override=OUTCOME_REJECTED),
        )
        self.assertEqual(outcome, OUTCOME_REJECTED)


class ParseRequestBodyTestCase(unittest.TestCase):
    def test_parses_json_object(self):
        body = parse_request_body(
            b'{"payer_name":"Alice","amount":"10"}',
            "application/json",
        )
        self.assertEqual(body, {"payer_name": "Alice", "amount": "10"})

    def test_invalid_json_wraps_raw(self):
        body = parse_request_body(b"not-json", "application/json")
        self.assertIn("_raw", body)


class RecordWebhookIngestRequestTestCase(unittest.TestCase):
    @patch("api.webhook_ingest_audit.get_db")
    def test_writes_row(self, mock_get_db):
        session = MagicMock()

        def _add(row):
            row.id = 99

        session.add.side_effect = _add
        session.flush.side_effect = None
        mock_get_db.return_value.__enter__.return_value = session

        row_id = record_webhook_ingest_request(
            source="venmo",
            endpoint_path="/api/venmo/payments",
            http_status_code=200,
            outcome=OUTCOME_SUCCESS_CREATED,
            duration_ms=12,
            ctx=WebhookIngestContext(
                payment_id=42,
                request_body={"amount": "10"},
                response_json={"created": True},
            ),
        )
        self.assertEqual(row_id, 99)
        session.add.assert_called_once()

    @patch("api.webhook_ingest_audit.get_db")
    def test_db_failure_does_not_raise(self, mock_get_db):
        mock_get_db.return_value.__enter__.side_effect = RuntimeError("db down")
        row_id = record_webhook_ingest_request(
            source="venmo",
            endpoint_path="/api/venmo/payments",
            http_status_code=500,
            outcome=OUTCOME_PROCESSING_ERROR,
            duration_ms=1,
            ctx=WebhookIngestContext(),
        )
        self.assertIsNone(row_id)


class SourceForPathTestCase(unittest.TestCase):
    def test_known_paths(self):
        self.assertEqual(source_for_path("/api/venmo/payments"), "venmo")
        self.assertEqual(source_for_path("/api/stripe/webhook"), "stripe")
        self.assertIsNone(source_for_path("/api/clubs"))


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(WebhookIngestMiddleware)
    app.include_router(router)
    return app


class VenmoWebhookIngestIntegrationTestCase(unittest.TestCase):
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

    @patch("api.webhook_ingest_audit.record_webhook_ingest_request")
    @patch("api.routes.venmo_payments.ingest_venmo_payment", new_callable=AsyncMock)
    def test_success_records_audit_with_request_body(self, mock_ingest, mock_record):
        mock_ingest.return_value = IngestResult(
            payment_id=42,
            status="unbound",
            auto_bound=False,
            created=True,
        )
        response = self.client.post(
            "/api/venmo/payments",
            json={
                "payer_name": "Moshe Toussoun",
                "amount": "200.00",
                "venmo_handle": "@godfather4444",
                "method_owner": "round-table",
            },
            headers={"X-Venmo-Webhook-Secret": WEBHOOK_SECRET},
        )
        self.assertEqual(response.status_code, 200)
        mock_record.assert_called_once()
        kwargs = mock_record.call_args.kwargs
        self.assertEqual(kwargs["source"], "venmo")
        self.assertEqual(kwargs["http_status_code"], 200)
        self.assertEqual(kwargs["outcome"], OUTCOME_SUCCESS_CREATED)
        self.assertEqual(kwargs["ctx"].payment_id, 42)
        self.assertEqual(kwargs["ctx"].request_body["payer_name"], "Moshe Toussoun")

    @patch("api.webhook_ingest_audit.record_webhook_ingest_request")
    def test_auth_failed_records_audit(self, mock_record):
        response = self.client.post(
            "/api/venmo/payments",
            json={
                "payer_name": "Moshe Toussoun",
                "amount": "200.00",
                "venmo_handle": "@godfather4444",
                "method_owner": "round-table",
            },
        )
        self.assertEqual(response.status_code, 401)
        mock_record.assert_called_once()
        kwargs = mock_record.call_args.kwargs
        self.assertEqual(kwargs["outcome"], OUTCOME_AUTH_FAILED)
        self.assertEqual(kwargs["ctx"].request_body["amount"], "200.00")


if __name__ == "__main__":
    unittest.main()
