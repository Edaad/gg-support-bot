"""Tests for cross-club audit XLSX export."""

from __future__ import annotations

import io
import os
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from api.auth import create_token, get_current_admin
from api.audit_export import (
    SHEET_SPECS,
    ManualAuditRow,
    StripeAuditRow,
    _fmt_manual_audit_time,
    _fmt_stripe_audit_time,
    _manual_club_cell,
    _stripe_player_cell,
    build_audit_workbook,
    eastern_day_bounds_utc,
)
from api.payments_helpers import build_crypto_payment_read
from api.routes.payments import router
from bot.services.crypto_payments import ALERT_SCOPE_LABELS, ALERT_SCOPE_CLUBGTO
from db.connection import get_db_dependency
from db.models import CryptoPayment

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


class AuditExportFormattingTestCase(unittest.TestCase):
    def test_eastern_day_bounds_utc_edt(self):
        start, end = eastern_day_bounds_utc("2026-06-19")
        self.assertEqual(start, datetime(2026, 6, 19, 4, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 6, 20, 3, 59, 59, 999999, tzinfo=timezone.utc))

    def test_eastern_day_bounds_utc_est(self):
        start, end = eastern_day_bounds_utc("2026-01-15")
        self.assertEqual(start, datetime(2026, 1, 15, 5, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 1, 16, 4, 59, 59, 999999, tzinfo=timezone.utc))

    def test_eastern_day_bounds_utc_accepts_iso_prefix(self):
        start, end = eastern_day_bounds_utc("2026-06-19T00:00:00Z")
        self.assertEqual(start, datetime(2026, 6, 19, 4, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 6, 20, 3, 59, 59, 999999, tzinfo=timezone.utc))

    def test_fmt_stripe_audit_time_uses_ordinal_eastern(self):
        dt = datetime(2026, 6, 19, 4, 58, tzinfo=timezone.utc)
        self.assertEqual(_fmt_stripe_audit_time(dt), "Jun 19th 2026, 12:58 AM")

    def test_fmt_manual_audit_time_uses_full_month_and_at(self):
        dt = datetime(2026, 6, 19, 4, 34, tzinfo=timezone.utc)
        self.assertEqual(_fmt_manual_audit_time(dt), "June 19, 2026 at 12:34 AM")

    def test_stripe_player_cell_uses_group_title_when_present(self):
        self.assertEqual(
            _stripe_player_cell(
                group_title="GTO / 3011-9668 / Pvtenis",
                club_name="ClubGTO",
                gg_player_id="3011-9668",
                gg_nickname="Pvtenis",
            ),
            "GTO / 3011-9668 / Pvtenis",
        )

    def test_stripe_player_cell_builds_fallback(self):
        self.assertEqual(
            _stripe_player_cell(
                group_title=None,
                club_name="ClubGTO",
                gg_player_id="3011-9668",
                gg_nickname="Pvtenis",
            ),
            "GTO / 3011-9668 / Pvtenis",
        )

    def test_manual_club_cell_reads_provider_label(self):
        self.assertEqual(
            _manual_club_cell({"zelle_recipient": "RT/AT/CC"}, "zelle_recipient"),
            "RT/AT/CC",
        )


class AuditExportWorkbookTestCase(unittest.TestCase):
    def test_alert_scope_labels_importable(self):
        self.assertIn(ALERT_SCOPE_CLUBGTO, ALERT_SCOPE_LABELS)

    def test_build_crypto_payment_read_does_not_raise(self):
        payment = CryptoPayment(
            id=1,
            amount_cents=10000,
            token_symbol="USDC",
            chain="ethereum",
            from_address="0xfrom1234567890abcdef",
            to_address="0xto",
            transaction_hash="0xhash",
            alert_scope=ALERT_SCOPE_CLUBGTO,
            is_test=False,
            auto_bound=False,
            created_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
        data = build_crypto_payment_read(MagicMock(), payment)
        self.assertEqual(data["alert_scope_label"], ALERT_SCOPE_LABELS[ALERT_SCOPE_CLUBGTO])
        self.assertIn("0xfrom", data["from_label"])

    @patch("api.audit_export._fetch_stripe_rows", return_value=[])
    @patch("api.audit_export._fetch_manual_rows", return_value=[])
    @patch("api.audit_export._club_name_map", return_value={})
    def test_build_audit_workbook_has_seven_sheets_with_headers(
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
        self.assertEqual(wb.sheetnames, [spec.title for spec in SHEET_SPECS])
        for spec in SHEET_SPECS:
            ws = wb[spec.title]
            self.assertEqual([cell.value for cell in ws[1]], spec.headers)

    @patch("api.audit_export._fetch_manual_rows", return_value=[])
    @patch("api.audit_export._club_name_map", return_value={})
    def test_build_audit_workbook_styles_stripe_sheet(self, _club_map, _manual):
        stripe_rows = [
            StripeAuditRow(
                amount_usd=42.0,
                player="GTO / 3011-9668 / Pvtenis",
                time_label="Jun 19th 2026, 12:58 AM",
                stripe_fee_usd=Decimal("1.52"),
            )
        ]

        with patch("api.audit_export._fetch_stripe_rows", return_value=stripe_rows):
            content = build_audit_workbook(
                MagicMock(),
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 31, tzinfo=timezone.utc),
            )

        wb = load_workbook(io.BytesIO(content))
        ws = wb["Stripe"]
        header = ws["A1"]
        self.assertEqual(header.value, "Amount")
        self.assertEqual(header.fill.start_color.rgb, "0038761D")
        self.assertTrue(header.font.bold)
        self.assertEqual(header.font.color.rgb, "00FFFFFF")

        amount_cell = ws["A2"]
        self.assertEqual(amount_cell.value, 42.0)
        self.assertEqual(amount_cell.number_format, "$#,##0.00")
        self.assertIsNotNone(amount_cell.comment)
        self.assertIn("Stripe fee", amount_cell.comment.text)

        zelle_ws = wb["Zelle"]
        self.assertEqual(zelle_ws.max_row, 1)


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

    @patch("api.routes.payments.build_audit_workbook")
    def test_audit_export_uses_eastern_day_bounds(self, mock_build):
        mock_build.return_value = b"fake-xlsx"
        response = self.client.get(
            "/api/payments/audit-export",
            params={"from": "2026-06-19", "to": "2026-06-19"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        mock_build.assert_called_once()
        _, from_dt, to_dt = mock_build.call_args[0]
        self.assertEqual(from_dt, datetime(2026, 6, 19, 4, 0, tzinfo=timezone.utc))
        self.assertEqual(to_dt, datetime(2026, 6, 20, 3, 59, 59, 999999, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
