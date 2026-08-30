"""API tests for manual deposit request list filters and summary."""

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
from api.routes.manual_deposit_requests import router
from db.connection import get_db_dependency

TOKEN = create_token()


def _row(**kwargs):
    base = {
        "id": 1,
        "club_id": 2,
        "method_id": 10,
        "method_name": "Zelle",
        "method_slug": "zelle-union-1",
        "variant_name": "pay@union.example",
        "group_title": "RT / 1111-2222 / Player",
        "telegram_chat_id": -100,
        "amount": Decimal("250.00"),
        "trade_record_checked": True,
        "source": "bot",
        "created_at": datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc),
        "club": SimpleNamespace(id=2, name="Round Table"),
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


class ManualDepositRequestsApiTestCase(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {"DASHBOARD_PASSWORD": "changeme"}, clear=False)
        self.env_patch.start()
        self.app = FastAPI()
        self.app.include_router(router)
        self.app.dependency_overrides[get_current_admin] = lambda: "admin"
        self.mock_db = MagicMock()

        def override_db():
            yield self.mock_db

        self.app.dependency_overrides[get_db_dependency] = override_db
        self.list_query_patch = patch("api.routes.manual_deposit_requests._list_query")
        self.mock_list_query = self.list_query_patch.start()
        self.query = MagicMock()
        self.mock_list_query.return_value = self.query

    def tearDown(self):
        self.list_query_patch.stop()
        self.env_patch.stop()

    def _set_rows(self, rows: list) -> TestClient:
        total_amount = sum(Decimal(str(r.amount)) for r in rows)
        summary_row = SimpleNamespace(total_count=len(rows), total_amount=total_amount)
        self.query.with_entities.return_value.one.return_value = summary_row
        self.query.offset.return_value.limit.return_value.all.return_value = rows
        return TestClient(self.app)

    def test_list_includes_summary(self):
        client = self._set_rows([_row(), _row(id=2, amount=Decimal("100.00"))])
        response = client.get(
            "/api/manual-deposit-requests?trade_record_checked=true",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["summary"]["total_count"], 2)
        self.assertEqual(Decimal(data["summary"]["total_amount"]), Decimal("350.00"))

    def test_list_passes_variant_and_dates(self):
        client = self._set_rows([_row()])
        response = client.get(
            "/api/manual-deposit-requests"
            "?trade_record_checked=true"
            "&variant=pay@union.example"
            "&from=2024-06-01T00:00:00Z"
            "&to=2024-06-30T23:59:59Z",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        kwargs = self.mock_list_query.call_args.kwargs
        self.assertEqual(kwargs["variant"], "pay@union.example")
        self.assertIsNotNone(kwargs["from_dt"])
        self.assertIsNotNone(kwargs["to_dt"])

    def test_variants_endpoint(self):
        self.query.with_entities.return_value.distinct.return_value.order_by.return_value.all.return_value = [
            ("pay@a",),
            ("pay@b",),
        ]
        client = TestClient(self.app)
        response = client.get(
            "/api/manual-deposit-requests/variants?trade_record_checked=true",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], ["pay@a", "pay@b"])
