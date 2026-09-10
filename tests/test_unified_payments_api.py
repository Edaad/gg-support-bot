"""API tests for unified payments (method=all, All tab)."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from api.auth import create_token, get_current_admin
from api.routes.all_payments import router as all_payments_router
from api.routes.owner_payments import router as owner_payments_router
from api.routes.payments_export import router as payments_export_router
from api.schemas_payments import OwnerPaymentSummary, UnifiedPaymentRowRead
from api.unified_payments import PaymentSourceSpec, _ingest_occurred_at, _merge_rows, resolve_sources
from db.connection import get_db_dependency

TOKEN = create_token()


class IngestOccurredAtTests(unittest.TestCase):
    def test_crypto_prefers_paid_at_over_created_at(self):
        occurred = _ingest_occurred_at(
            "crypto",
            {
                "paid_at": "2026-08-14T00:17:21Z",
                "created_at": "2026-09-05T05:48:24.165413+00:00",
            },
        )
        self.assertEqual(occurred, datetime(2026, 8, 14, 0, 17, 21, tzinfo=timezone.utc))

    def test_falls_back_to_created_at_when_paid_at_missing(self):
        created = "2026-09-05T05:48:24.165413+00:00"
        self.assertEqual(
            _ingest_occurred_at("crypto", {"paid_at": None, "created_at": created}),
            created,
        )

    def test_non_crypto_ignores_paid_at(self):
        created = "2026-09-05T05:48:24.165413+00:00"
        self.assertEqual(
            _ingest_occurred_at(
                "venmo",
                {"paid_at": "2026-08-14T00:17:21Z", "created_at": created},
            ),
            created,
        )

    def test_stripe_prefers_completed_at(self):
        self.assertEqual(
            _ingest_occurred_at(
                "stripe",
                {
                    "completed_at": "2026-09-01T12:00:00Z",
                    "created_at": "2026-09-01T11:00:00Z",
                },
            ),
            "2026-09-01T12:00:00Z",
        )


def _make_app(*routers) -> FastAPI:
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    app.dependency_overrides[get_current_admin] = lambda: "admin"

    def override_db():
        yield MagicMock()

    app.dependency_overrides[get_db_dependency] = override_db
    return app


def _sample_row(
    *,
    source: str = "venmo",
    row_id: int = 1,
    occurred_at: datetime | None = None,
    owner_label: str = "RT",
    status: str | None = "bound",
) -> UnifiedPaymentRowRead:
    ts = occurred_at or datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    return UnifiedPaymentRowRead(
        source=source,  # type: ignore[arg-type]
        id=row_id,
        occurred_at=ts,
        amount_cents=1000,
        amount_usd=Decimal("10.00"),
        method_slug=source if source != "union_manual" else "zelle",
        method_label="Venmo",
        owner_label=owner_label,
        group_title="RT / 1 / Test",
        gg_nickname="Test",
        club_id=1,
        status=status,
        variant=None,
        can_bind=False,
        detail={"id": row_id},
    )


class UnifiedPaymentsHelperTestCase(unittest.TestCase):
    def test_resolve_sources_all_scope_includes_union(self):
        sources = resolve_sources("all", None, "all")
        kinds = {s.kind for s in sources}
        self.assertIn("union_manual", kinds)
        self.assertIn("stripe", kinds)
        self.assertIn("venmo", kinds)

    def test_resolve_sources_owner_scope_excludes_union(self):
        sources = resolve_sources("owner", "round-table", "all")
        self.assertTrue(all(s.kind != "union_manual" for s in sources))
        self.assertTrue(any(s.kind == "stripe" for s in sources))

    def test_merge_rows_orders_by_time_desc(self):
        older = _sample_row(row_id=1, occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        newer = _sample_row(row_id=2, occurred_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        merged = _merge_rows(
            [
                (
                    PaymentSourceSpec(kind="venmo", owner_slug="round-table", method_slug="venmo"),
                    [older],
                ),
                (
                    PaymentSourceSpec(kind="venmo", owner_slug="vaughn", method_slug="venmo"),
                    [newer],
                ),
            ],
            offset=0,
            limit=10,
        )
        self.assertEqual(merged[0].id, 2)
        self.assertEqual(merged[1].id, 1)


class UnifiedPaymentsApiTestCase(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {"DASHBOARD_PASSWORD": "changeme"}, clear=False)
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()

    def test_all_payments_requires_auth(self):
        app = FastAPI()
        app.include_router(all_payments_router)
        client = TestClient(app)
        response = client.get("/api/payments/all/payments")
        self.assertIn(response.status_code, (401, 403))

    @patch("api.routes.all_payments.fetch_unified_page")
    def test_all_payments_includes_union_rows(self, mock_fetch):
        union_row = _sample_row(source="union_manual", owner_label="Union", status=None)
        mock_fetch.return_value = (
            [union_row],
            1,
            OwnerPaymentSummary(
                total_count=1,
                total_amount_cents=1000,
                total_amount_usd=Decimal("10.00"),
            ),
        )
        client = TestClient(_make_app(all_payments_router))
        response = client.get(
            "/api/payments/all/payments?method=all",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["items"][0]["owner_label"], "Union")
        self.assertIsNone(body["items"][0]["status"])

    @patch("api.routes.owner_payments.fetch_unified_page")
    def test_owner_method_all_returns_unified(self, mock_fetch):
        mock_fetch.return_value = (
            [],
            0,
            OwnerPaymentSummary(
                total_count=0,
                total_amount_cents=0,
                total_amount_usd=Decimal("0"),
            ),
        )
        client = TestClient(_make_app(owner_payments_router))
        response = client.get(
            "/api/payments/owner/round-table/payments?method=all",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["method"], "all")

    @patch("api.routes.owner_payments.fetch_unified_page")
    def test_owner_method_all_rejects_variant(self, mock_fetch):
        client = TestClient(_make_app(owner_payments_router))
        response = client.get(
            "/api/payments/owner/round-table/payments?method=all&variant=foo",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 400)
        mock_fetch.assert_not_called()

    @patch("api.routes.payments_export.fetch_all_unified_rows")
    @patch("api.payments_export.build_payments_workbook")
    def test_export_xlsx_returns_workbook(self, mock_build, mock_fetch):
        mock_fetch.return_value = (
            [_sample_row()],
            OwnerPaymentSummary(
                total_count=1,
                total_amount_cents=1000,
                total_amount_usd=Decimal("10"),
            ),
        )
        mock_build.return_value = _build_minimal_xlsx()
        client = TestClient(_make_app(payments_export_router))
        response = client.get(
            "/api/payments/all/export.xlsx?method=all",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response.headers.get("content-type", ""))


def _build_minimal_xlsx() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["time", "amount"])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


if __name__ == "__main__":
    unittest.main()
