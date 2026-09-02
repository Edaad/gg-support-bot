"""Tests for payment ingest head-admin escalation."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.venmo_payments import router
from api.webhook_ingest_audit import WebhookIngestContext, WebhookIngestMiddleware
from bot.services import payment_ingest_escalation as pie
from bot.services.venmo_payments import WEBHOOK_SECRET_ENV, IngestResult

WEBHOOK_SECRET = "test-venmo-webhook-secret"


class ShouldAlertIngestTestCase(unittest.TestCase):
    def test_http_error(self):
        self.assertEqual(
            pie.should_alert_ingest(http_status_code=503, response_json=None),
            pie.ALERT_HTTP_ERROR,
        )

    def test_not_created(self):
        self.assertEqual(
            pie.should_alert_ingest(
                http_status_code=200,
                response_json={"created": False},
            ),
            pie.ALERT_NOT_CREATED,
        )

    def test_created_true_no_alert(self):
        self.assertIsNone(
            pie.should_alert_ingest(
                http_status_code=200,
                response_json={"created": True},
            )
        )

    def test_stripe_200_no_created_no_alert(self):
        self.assertIsNone(
            pie.should_alert_ingest(http_status_code=200, response_json=None)
        )


class IsTestIngestTestCase(unittest.TestCase):
    def test_is_test_flag(self):
        self.assertTrue(pie.is_test_ingest(is_test=True, request_body=None))

    def test_body_test_true(self):
        self.assertTrue(
            pie.is_test_ingest(is_test=None, request_body={"test": True})
        )

    def test_production(self):
        self.assertFalse(
            pie.is_test_ingest(is_test=False, request_body={"test": False})
        )


class FormatIngestEscalationTextTestCase(unittest.TestCase):
    def test_http_error_shape(self):
        text = pie.format_ingest_escalation_text(
            alert_kind=pie.ALERT_HTTP_ERROR,
            source="venmo",
            amount_cents=5000,
            error_message="Telegram notification failed",
        )
        self.assertEqual(
            text,
            "Payment ingest failure\n"
            "source: venmo\n"
            "amount: $50.00\n"
            "error: Telegram notification failed",
        )
        self.assertNotIn("payment_id", text)
        self.assertNotIn("outcome", text)

    def test_not_created_shape(self):
        text = pie.format_ingest_escalation_text(
            alert_kind=pie.ALERT_NOT_CREATED,
            source="zelle",
            amount_cents=10000,
            error_message=None,
        )
        self.assertEqual(
            text,
            "Payment ingest not created\n"
            "source: zelle\n"
            "amount: $100.00\n"
            "error: created=false (duplicate)",
        )

    def test_unknown_amount(self):
        text = pie.format_ingest_escalation_text(
            alert_kind=pie.ALERT_HTTP_ERROR,
            source="stripe",
            amount_cents=None,
            error_message="Invalid Stripe signature",
        )
        self.assertIn("amount: unknown", text)


class MaybeNotifyPaymentIngestEscalationTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_http_error_notifies(self):
        ctx = WebhookIngestContext(
            amount_cents=5000,
            error_message="Telegram notification failed",
            source_external_id="txn_1",
        )
        with (
            patch.object(pie, "recent_ingest_alert_exists", return_value=False),
            patch(
                "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
                new_callable=AsyncMock,
                return_value=True,
            ) as notify,
        ):
            ok = await pie.maybe_notify_payment_ingest_escalation(
                source="venmo",
                http_status_code=503,
                ctx=ctx,
                recorded_id=1,
            )
        self.assertTrue(ok)
        notify.assert_awaited_once()
        text, kwargs = notify.await_args.args[0], notify.await_args.kwargs
        self.assertTrue(text.startswith("Payment ingest failure"))
        self.assertEqual(kwargs["source"], pie.SOURCE_HTTP_ERROR)
        self.assertIn("source: venmo", text)
        self.assertIn("amount: $50.00", text)
        self.assertIn("error: Telegram notification failed", text)

    async def test_not_created_notifies(self):
        ctx = WebhookIngestContext(
            amount_cents=10000,
            response_json={"created": False},
            source_external_id="zelle_1",
            payment_id=99,
        )
        with (
            patch.object(pie, "recent_ingest_alert_exists", return_value=False),
            patch(
                "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
                new_callable=AsyncMock,
                return_value=True,
            ) as notify,
        ):
            ok = await pie.maybe_notify_payment_ingest_escalation(
                source="zelle",
                http_status_code=200,
                ctx=ctx,
                recorded_id=2,
            )
        self.assertTrue(ok)
        text = notify.await_args.args[0]
        self.assertTrue(text.startswith("Payment ingest not created"))
        self.assertEqual(
            notify.await_args.kwargs["source"], pie.SOURCE_NOT_CREATED
        )

    async def test_created_true_skips(self):
        ctx = WebhookIngestContext(response_json={"created": True})
        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new_callable=AsyncMock,
        ) as notify:
            ok = await pie.maybe_notify_payment_ingest_escalation(
                source="venmo",
                http_status_code=200,
                ctx=ctx,
                recorded_id=3,
            )
        self.assertFalse(ok)
        notify.assert_not_awaited()

    async def test_is_test_skips(self):
        ctx = WebhookIngestContext(
            is_test=True,
            error_message="boom",
            response_json=None,
        )
        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new_callable=AsyncMock,
        ) as notify:
            ok = await pie.maybe_notify_payment_ingest_escalation(
                source="venmo",
                http_status_code=503,
                ctx=ctx,
                recorded_id=4,
            )
        self.assertFalse(ok)
        notify.assert_not_awaited()

    async def test_body_test_skips(self):
        ctx = WebhookIngestContext(
            request_body={"test": True},
            error_message="boom",
        )
        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new_callable=AsyncMock,
        ) as notify:
            ok = await pie.maybe_notify_payment_ingest_escalation(
                source="venmo",
                http_status_code=503,
                ctx=ctx,
                recorded_id=5,
            )
        self.assertFalse(ok)
        notify.assert_not_awaited()

    async def test_dedupe_skips_second(self):
        ctx = WebhookIngestContext(
            amount_cents=100,
            error_message="fail",
            source_external_id="txn_dup",
        )
        with (
            patch.object(pie, "recent_ingest_alert_exists", return_value=True),
            patch(
                "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
                new_callable=AsyncMock,
            ) as notify,
        ):
            ok = await pie.maybe_notify_payment_ingest_escalation(
                source="venmo",
                http_status_code=503,
                ctx=ctx,
                recorded_id=6,
            )
        self.assertFalse(ok)
        notify.assert_not_awaited()


class RecentIngestAlertExistsTestCase(unittest.TestCase):
    @patch("bot.services.payment_ingest_escalation.get_db")
    def test_finds_prior_http_error(self, mock_get_db):
        session = MagicMock()
        q = MagicMock()
        session.query.return_value = q
        q.filter.return_value = q
        q.first.return_value = SimpleNamespace(id=1)
        mock_get_db.return_value.__enter__.return_value = session

        exists = pie.recent_ingest_alert_exists(
            source="venmo",
            alert_kind=pie.ALERT_HTTP_ERROR,
            dedupe_key="ext:txn_1",
            exclude_id=99,
        )
        self.assertTrue(exists)

    @patch("bot.services.payment_ingest_escalation.get_db")
    def test_db_failure_returns_false(self, mock_get_db):
        mock_get_db.return_value.__enter__.side_effect = RuntimeError("db down")
        exists = pie.recent_ingest_alert_exists(
            source="venmo",
            alert_kind=pie.ALERT_HTTP_ERROR,
            dedupe_key="none",
            exclude_id=None,
        )
        self.assertFalse(exists)


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(WebhookIngestMiddleware)
    app.include_router(router)
    return app


class MiddlewareEscalationIntegrationTestCase(unittest.TestCase):
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

    @patch("api.webhook_ingest_audit.record_webhook_ingest_request", return_value=10)
    @patch(
        "bot.services.payment_ingest_escalation.maybe_notify_payment_ingest_escalation",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("api.routes.venmo_payments.ingest_venmo_payment", new_callable=AsyncMock)
    def test_success_created_calls_notify_helper(
        self, mock_ingest, mock_notify, _mock_record
    ):
        mock_ingest.return_value = IngestResult(
            payment_id=42,
            status="unbound",
            auto_bound=False,
            created=True,
        )
        response = self.client.post(
            "/api/venmo/payments",
            json={
                "payer_name": "Alice",
                "amount": "50.00",
                "venmo_handle": "@alice",
                "method_owner": "round-table",
            },
            headers={"X-Venmo-Webhook-Secret": WEBHOOK_SECRET},
        )
        self.assertEqual(response.status_code, 200)
        mock_notify.assert_awaited_once()
        self.assertEqual(mock_notify.await_args.kwargs["http_status_code"], 200)
        self.assertEqual(
            mock_notify.await_args.kwargs["ctx"].response_json["created"], True
        )

    @patch("api.webhook_ingest_audit.record_webhook_ingest_request", return_value=11)
    @patch(
        "bot.services.payment_ingest_escalation.maybe_notify_payment_ingest_escalation",
        new_callable=AsyncMock,
        return_value=True,
    )
    def test_auth_failed_calls_notify_helper(self, mock_notify, _mock_record):
        response = self.client.post(
            "/api/venmo/payments",
            json={
                "payer_name": "Alice",
                "amount": "50.00",
                "venmo_handle": "@alice",
                "method_owner": "round-table",
            },
        )
        self.assertEqual(response.status_code, 401)
        mock_notify.assert_awaited_once()
        self.assertEqual(mock_notify.await_args.kwargs["http_status_code"], 401)


if __name__ == "__main__":
    unittest.main()
