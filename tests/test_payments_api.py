"""API tests for payments dashboard routes."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import create_token, get_current_admin
from api.routes.payments import (
    BOUND_VIA_FILTER_ALIASES,
    _resolve_bound_via_filter,
    router,
)
from db.connection import get_db_dependency
from db.models import PaymentMethodBindAttempt

TOKEN = create_token()


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    def override_admin():
        return "admin"

    def override_db():
        yield MagicMock()

    app.dependency_overrides[get_current_admin] = override_admin
    app.dependency_overrides[get_db_dependency] = override_db
    return app


class PaymentsApiTestCase(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {"DASHBOARD_PASSWORD": "changeme"}, clear=False)
        self.env_patch.start()
        self.client = TestClient(_make_app())

    def tearDown(self):
        self.env_patch.stop()

    def test_providers_requires_auth(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        response = client.get("/api/payments/providers")
        self.assertIn(response.status_code, (401, 403))

    def test_providers_success(self):
        response = self.client.get(
            "/api/payments/providers",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)
        ids = {row["id"] for row in data}
        self.assertEqual(ids, {"stripe", "venmo"})

    def test_venmo_payments_club_not_found(self):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_admin] = lambda: "admin"

        def override_db():
            yield mock_db

        app.dependency_overrides[get_db_dependency] = override_db
        client = TestClient(app)

        response = client.get(
            "/api/payments/venmo/payments?club_id=999",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 404)

    def test_venmo_bind_empty_title(self):
        response = self.client.post(
            "/api/payments/venmo/payments/1/bind",
            json={"group_title": "  "},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertIn("required", data["error"].lower())

    def test_venmo_bind_success(self):
        from bot.services.venmo_payments import BindResult, BoundGroup

        with patch(
            "api.routes.payments.bind_venmo_payment_by_id",
            new=AsyncMock(
                return_value=BindResult(
                    ok=True,
                    bound_group=BoundGroup(
                        telegram_chat_id=-1001,
                        club_id=1,
                        group_title="RT / 1234-5678 / Test",
                    ),
                )
            ),
        ):
            mock_db = MagicMock()
            mock_payment = MagicMock()
            mock_payment.id = 1
            mock_db.query.return_value.filter.return_value.first.return_value = mock_payment

            app = FastAPI()
            app.include_router(router)
            app.dependency_overrides[get_current_admin] = lambda: "admin"

            def override_db():
                yield mock_db

            app.dependency_overrides[get_db_dependency] = override_db
            client = TestClient(app)

            with patch(
                "api.routes.payments.build_venmo_payment_read",
                return_value={
                    "id": 1,
                    "payer_name": "Test",
                    "venmo_handle": "@test",
                    "amount_cents": 10000,
                    "amount_usd": "100.00",
                    "goods_or_services": False,
                    "paid_at": None,
                    "group_title": "RT / 1234-5678 / Test",
                    "gg_player_id": "1234-5678",
                    "gg_nickname": None,
                    "club_id": 1,
                    "telegram_chat_id": -1001,
                    "status": "bound",
                    "auto_bound": False,
                    "is_test": False,
                    "created_at": "2024-01-01T00:00:00Z",
                    "bound_at": "2024-01-01T00:00:00Z",
                },
            ):
                response = client.post(
                    "/api/payments/venmo/payments/1/bind",
                    json={"group_title": "RT / 1234-5678 / Test"},
                    headers={"Authorization": f"Bearer {TOKEN}"},
                )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["group_title"], "RT / 1234-5678 / Test")

    def test_resolve_bound_via_filter_manual_alias(self):
        self.assertEqual(
            _resolve_bound_via_filter("manual"),
            BOUND_VIA_FILTER_ALIASES["manual"],
        )

    def test_resolve_bound_via_filter_single_value(self):
        self.assertEqual(_resolve_bound_via_filter("special_amount"), ("special_amount",))

    def test_resolve_bound_via_filter_all_ignored(self):
        self.assertIsNone(_resolve_bound_via_filter(None))
        self.assertIsNone(_resolve_bound_via_filter("all"))
        self.assertIsNone(_resolve_bound_via_filter("  "))

    def test_bindings_summary_total_bound(self):
        binding_rows = [("backfill", 3), ("special_amount", 2)]
        bind_kind_rows = [("special_amount", 4), ("memo_emoji", 1)]

        mock_binding_q = MagicMock()
        mock_binding_q.filter.return_value = mock_binding_q
        mock_binding_q.group_by.return_value.all.return_value = binding_rows

        mock_bind_kind_q = MagicMock()
        mock_bind_kind_q.filter.return_value = mock_bind_kind_q
        mock_bind_kind_q.group_by.return_value.all.return_value = bind_kind_rows

        mock_attempt = MagicMock()
        mock_attempt.status = "succeeded"
        mock_attempt_q = MagicMock()
        mock_attempt_q.filter.return_value = mock_attempt_q
        mock_attempt_q.all.return_value = [mock_attempt, mock_attempt]

        mock_db = MagicMock()

        def query_side_effect(*args):
            if len(args) == 1 and args[0] is PaymentMethodBindAttempt:
                return mock_attempt_q
            if len(args) >= 2:
                col_keys = {getattr(a, "key", None) for a in args}
                if "bind_kind" in col_keys:
                    return mock_bind_kind_q
            return mock_binding_q

        mock_db.query.side_effect = query_side_effect

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_admin] = lambda: "admin"

        def override_db():
            yield mock_db

        app.dependency_overrides[get_db_dependency] = override_db
        client = TestClient(app)

        response = client.get(
            "/api/payments/bindings/summary?method=venmo",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total_bound"], 5)
        self.assertEqual(len(data["bindings_by_via"]), 2)
        self.assertEqual(len(data["attempts_by_bind_kind"]), 2)
        self.assertEqual(data["attempt_funnel"]["initiated"], 2)
        self.assertEqual(data["attempt_funnel"]["succeeded"], 2)

    def test_customers_club_not_found(self):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_admin] = lambda: "admin"

        def override_db():
            yield mock_db

        app.dependency_overrides[get_db_dependency] = override_db
        client = TestClient(app)

        response = client.get(
            "/api/payments/stripe/customers?club_id=999",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
