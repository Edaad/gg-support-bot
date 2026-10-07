"""Service reconcile export: secret header, no dashboard login."""

from __future__ import annotations

import os
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.audit import (
    AUDIT_EXPORT_SECRET_HEADER,
    AUDIT_EXPORT_WEBHOOK_SECRET_ENV,
    service_router,
)
from db.connection import get_db_dependency

SECRET = "test-audit-export-secret"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _app() -> TestClient:
    app = FastAPI()
    app.include_router(service_router)
    app.dependency_overrides[get_db_dependency] = lambda: MagicMock()
    return TestClient(app)


def _files() -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (f"club-{i}.xlsx", b"x", XLSX)) for i in range(4)]


class ServiceReconcileExportTestCase(unittest.TestCase):
    def setUp(self):
        self.client = _app()

    def test_secret_unset(self):
        env = os.environ.copy()
        env.pop(AUDIT_EXPORT_WEBHOOK_SECRET_ENV, None)
        with patch.dict(os.environ, env, clear=True):
            response = self.client.post(
                "/api/audit/service/reconcile-export",
                files=_files(),
                headers={AUDIT_EXPORT_SECRET_HEADER: SECRET},
            )
        self.assertEqual(response.status_code, 503)

    def test_secret_rejected(self):
        with patch.dict(os.environ, {AUDIT_EXPORT_WEBHOOK_SECRET_ENV: SECRET}):
            response = self.client.post(
                "/api/audit/service/reconcile-export",
                files=_files(),
                headers={AUDIT_EXPORT_SECRET_HEADER: "nope"},
            )
        self.assertEqual(response.status_code, 401)

    @patch("api.routes.audit._matching_workbook_bytes", return_value=b"workbook")
    @patch("api.routes.audit._commit_four_trade_uploads")
    @patch("api.routes.audit._parsed_four_trade_files")
    def test_returns_workbook(self, parse, commit, _workbook):
        async def _parse(_files):
            return []

        parse.side_effect = _parse
        report = MagicMock()
        report.audit_date = date(2026, 10, 6)
        commit.return_value = [report]

        with patch.dict(os.environ, {AUDIT_EXPORT_WEBHOOK_SECRET_ENV: SECRET}):
            response = self.client.post(
                "/api/audit/service/reconcile-export",
                files=_files(),
                headers={AUDIT_EXPORT_SECRET_HEADER: SECRET},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, b"workbook")
        self.assertIn(
            "reconcile-all-clubs-2026-10-06.xlsx",
            response.headers["content-disposition"],
        )
