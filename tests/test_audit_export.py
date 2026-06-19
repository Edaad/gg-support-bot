"""Tests for cross-club audit XLSX export."""

from __future__ import annotations

import io
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from api.auth import create_token, get_current_admin
from api.audit_export import SHEET_SPECS, build_audit_workbook
from api.routes.payments import router
from db.connection import get_db_dependency

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


class AuditExportWorkbookTestCase(unittest.TestCase):
    @patch("api.audit_export._fetch_stripe_rows", return_value=[])
    @patch("api.audit_export._fetch_manual_rows", return_value=[])
    @patch("api.audit_export._club_name_map", return_value={})
    def test_build_audit_workbook_has_six_sheets_with_headers(
        self,
        _club_map,
        _manual,
        _stripe,
    ):
        session = MagicMock()
        from_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        to_dt = datetime(2026, 1, 31, 23, 59, 59, tzinfo=timezone.utc)
        content = build_audit_workbook(session, from_dt, to_dt)
        wb = load_workbook(io.BytesIO(content))
        self.assertEqual(wb.sheetnames, [title for title, _ in SHEET_SPECS])
        for title, headers in SHEET_SPECS:
            ws = wb[title]
            self.assertEqual([cell.value for cell in ws[1]], headers)


class AuditExportApiTestCase(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {"DASHBOARD_PASSWORD": "changeme"}, clear=False)
        self.env_patch.start()
        self.client = TestClient(_make_app())

    def tearDown(self):
        self.env_patch.stop()

    def test_audit_export_requires_auth(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        response = client.get(
            "/api/payments/audit-export",
            params={"from": "2026-01-01T00:00:00Z", "to": "2026-01-31T23:59:59Z"},
        )
        self.assertIn(response.status_code, (401, 403))

    def test_audit_export_requires_dates(self):
        response = self.client.get(
            "/api/payments/audit-export",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 422)

    def test_audit_export_rejects_inverted_range(self):
        response = self.client.get(
            "/api/payments/audit-export",
            params={"from": "2026-02-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("from must be on or before to", response.json()["detail"])

    @patch("api.routes.payments.build_audit_workbook")
    def test_audit_export_success(self, mock_build):
        mock_build.return_value = b"fake-xlsx"
        response = self.client.get(
            "/api/payments/audit-export",
            params={"from": "2026-01-01T00:00:00Z", "to": "2026-01-31T23:59:59Z"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["content-type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("audit-export-2026-01-01-2026-01-31.xlsx", response.headers["content-disposition"])
        self.assertEqual(response.content, b"fake-xlsx")
        mock_build.assert_called_once()


if __name__ == "__main__":
    unittest.main()
