"""API tests for owner-scoped payments dashboard routes."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import create_token, get_current_admin
from api.payments_helpers import (
    apply_owner_ingest_filters,
    apply_owner_stripe_filters,
    owner_payment_search_clause,
)
from api.routes.owner_payments import router
from db.connection import get_db_dependency
from db.models import VenmoPayment, ZellePayment

TOKEN = create_token()


def _make_app(mock_db: MagicMock | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_admin] = lambda: "admin"

    def override_db():
        yield mock_db or MagicMock()

    app.dependency_overrides[get_db_dependency] = override_db
    return app


class OwnerPaymentsApiTestCase(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {"DASHBOARD_PASSWORD": "changeme"}, clear=False)
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()

    def test_requires_auth(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        response = client.get("/api/payments/owner/round-table/payments?method=venmo")
        self.assertIn(response.status_code, (401, 403))

    def test_vaughn_rejects_crypto(self):
        client = TestClient(_make_app())
        response = client.get(
            "/api/payments/owner/vaughn/payments?method=crypto",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("not available", response.json()["detail"].lower())

    def test_stripe_only_on_round_table(self):
        client = TestClient(_make_app())
        response = client.get(
            "/api/payments/owner/vaughn/payments?method=stripe",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 400)

    def test_list_owner_payments_summary(self):
        mock_db = MagicMock()
        chain = mock_db.query.return_value
        chain.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            SimpleNamespace(id=1),
            SimpleNamespace(id=2),
        ]
        created = datetime(2024, 6, 1, tzinfo=timezone.utc)
        read_payload = {
            "id": 1,
            "payer_name": "A",
            "venmo_handle": "@a",
            "amount_cents": 7500,
            "amount_usd": Decimal("75.00"),
            "goods_or_services": False,
            "paid_at": None,
            "group_title": "RT / 1 / A",
            "gg_player_id": "1",
            "gg_nickname": None,
            "club_id": 2,
            "telegram_chat_id": -1,
            "status": "bound",
            "auto_bound": False,
            "is_test": False,
            "method_owner": "round-table",
            "created_at": created,
            "bound_at": created,
        }
        mock_read_model = MagicMock()
        mock_read_model.model_validate.side_effect = lambda payload: payload
        with (
            patch(
                "api.routes.owner_payments.apply_owner_ingest_filters",
                side_effect=lambda query, *args, **kwargs: query,
            ),
            patch(
                "api.routes.owner_payments.aggregate_owner_payment_query",
                return_value=(2, 15000),
            ),
            patch.dict(
                "api.routes.owner_payments._BUILD_READ_BY_METHOD",
                {"venmo": lambda _db, _row: read_payload},
            ),
            patch.dict(
                "api.routes.owner_payments._READ_MODEL_BY_METHOD",
                {"venmo": mock_read_model},
            ),
        ):
            client = TestClient(_make_app(mock_db))
            response = client.get(
                "/api/payments/owner/round-table/payments?method=venmo",
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["method"], "venmo")
        self.assertEqual(data["total"], 2)
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["summary"]["total_count"], 2)
        self.assertEqual(data["summary"]["total_amount_cents"], 15000)
        self.assertEqual(data["summary"]["total_amount_usd"], "150.00")

    def test_list_owner_payments_passes_club_id(self):
        mock_db = MagicMock()
        chain = mock_db.query.return_value
        chain.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        with (
            patch(
                "api.routes.owner_payments._get_club_or_404",
                return_value=SimpleNamespace(id=2),
            ) as mock_club,
            patch(
                "api.routes.owner_payments.apply_owner_ingest_filters",
                side_effect=lambda query, *args, **kwargs: query,
            ) as mock_filters,
            patch(
                "api.routes.owner_payments.aggregate_owner_payment_query",
                return_value=(0, 0),
            ),
            patch.dict(
                "api.routes.owner_payments._BUILD_READ_BY_METHOD",
                {"venmo": lambda _db, _row: {}},
            ),
            patch.dict(
                "api.routes.owner_payments._READ_MODEL_BY_METHOD",
                {"venmo": MagicMock(model_validate=lambda payload: payload)},
            ),
        ):
            client = TestClient(_make_app(mock_db))
            response = client.get(
                "/api/payments/owner/round-table/payments?method=venmo&club_id=2",
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
        self.assertEqual(response.status_code, 200)
        mock_club.assert_called_once_with(mock_db, 2)
        self.assertEqual(mock_filters.call_args.kwargs.get("club_id"), 2)

    @patch("api.routes.owner_payments.distinct_owner_ingest_variants")
    def test_list_owner_variants(self, mock_distinct):
        mock_distinct.return_value = ["@handle-a", "pay@example.com"]
        client = TestClient(_make_app())
        response = client.get(
            "/api/payments/owner/vaughn/variants?method=zelle",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["value"], "@handle-a")


class OwnerPaymentsHelperTestCase(unittest.TestCase):
    def test_apply_owner_ingest_filters_adds_owner_and_variant(self):
        query = MagicMock()
        payment_cls = VenmoPayment
        result = apply_owner_ingest_filters(
            query,
            payment_cls,
            method_owner="vaughn",
            variant="@janseashells",
            from_dt=None,
            to_dt=None,
            q=None,
        )
        self.assertIsNotNone(result)
        self.assertTrue(query.filter.called)

    @patch("api.payments_helpers._payment_linked_to_club")
    def test_apply_owner_ingest_filters_with_club_id(self, mock_linked):
        query = MagicMock()
        payment_cls = VenmoPayment
        mock_linked.return_value = "club_clause"
        apply_owner_ingest_filters(
            query,
            payment_cls,
            method_owner="round-table",
            variant=None,
            from_dt=None,
            to_dt=None,
            q=None,
            club_id=2,
        )
        mock_linked.assert_called_once_with(payment_cls, 2)

    def test_apply_owner_stripe_filters_with_club_id(self):
        query = MagicMock()
        apply_owner_stripe_filters(
            query,
            variant=None,
            from_dt=None,
            to_dt=None,
            q=None,
            club_id=3,
        )
        self.assertTrue(query.filter.called)

    def test_apply_owner_stripe_filters_manual_variant(self):
        query = MagicMock()
        result = apply_owner_stripe_filters(
            query,
            variant="manual",
            from_dt=None,
            to_dt=None,
            q=None,
        )
        self.assertIsNotNone(result)
        self.assertTrue(query.filter.called)

    def test_owner_payment_search_clause_builds(self):
        clause = owner_payment_search_clause(VenmoPayment.telegram_chat_id, "angus")
        self.assertIsNotNone(clause)
