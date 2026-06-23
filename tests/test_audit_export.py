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
    _manual_club_name,
    _manual_group_cell,
    _manual_row,
    _stripe_player_cell,
    build_audit_workbook,
    eastern_audit_end_utc,
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

    def test_eastern_audit_end_utc_edt(self):
        end = eastern_audit_end_utc("2026-06-21")
        self.assertEqual(end, datetime(2026, 6, 22, 4, 59, 59, 999999, tzinfo=timezone.utc))

    def test_eastern_audit_end_utc_est(self):
        end = eastern_audit_end_utc("2026-01-15")
        self.assertEqual(end, datetime(2026, 1, 16, 5, 59, 59, 999999, tzinfo=timezone.utc))

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

    def test_manual_group_cell_returns_bound_group_title(self):
        self.assertEqual(
            _manual_group_cell({"group_title": "GTO / 3011-9668 / Pvtenis"}),
            "GTO / 3011-9668 / Pvtenis",
        )

    def test_manual_group_cell_empty_when_unbound(self):
        self.assertEqual(_manual_group_cell({}), "")
        self.assertEqual(_manual_group_cell({"group_title": None}), "")

    def test_manual_club_name_from_club_id(self):
        club_names = {1: "ClubGTO"}
        self.assertEqual(
            _manual_club_name(
                {
                    "club_id": 1,
                    "group_title": "GTO / 3011-9668 / Pvtenis",
                },
                club_names,
            ),
            "ClubGTO",
        )

    def test_manual_club_name_falls_back_to_title_parsing(self):
        self.assertEqual(
            _manual_club_name(
                {"group_title": "GTO / 8190-5287 / ThePirate343"},
                {},
            ),
            "ClubGTO",
        )

    def test_manual_club_name_empty_when_unbound(self):
        self.assertEqual(
            _manual_club_name({"zelle_recipient": "clubgto1234@gmail.com"}, {}),
            "",
        )

    def test_manual_row_includes_group_and_club(self):
        created = datetime(2026, 6, 22, 1, 51, tzinfo=timezone.utc)
        row = _manual_row(
            {
                "amount_usd": Decimal("100.00"),
                "payer_name": "Jackson Taylor",
                "group_title": "RT / 6485-8168 / Angus Mcgoon",
                "club_id": 2,
                "created_at": created,
            },
            {2: "Round Table"},
        )
        self.assertEqual(row.amount_usd, 100.0)
        self.assertEqual(row.payer_name, "Jackson Taylor")
        self.assertEqual(row.group_title, "RT / 6485-8168 / Angus Mcgoon")
        self.assertEqual(row.club_label, "Round Table")
        self.assertEqual(row.time_label, _fmt_manual_audit_time(created))


class AuditExportWorkbookTestCase(unittest.TestCase):
    def test_alert_scope_labels_importable(self):
        self.assertIn(ALERT_SCOPE_CLUBGTO, ALERT_SCOPE_LABELS)

    def test_sheet_specs_use_title_case_tab_names(self):
        self.assertEqual(
            [spec.title for spec in SHEET_SPECS],
            [
                "Stripe",
                "Zelle",
                "Venmo",
                "Cash App",
                "PayPal",
                "Bonus",
                "Early Rakeback",
            ],
        )

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
                group_title="GTO / 3011-9668 / Pvtenis",
                club_label="ClubGTO",
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
        self.assertEqual(
            [cell.value for cell in ws[1]],
            ["Amount", "Player", "Group", "Club", "Time"],
        )
        self.assertEqual(ws["B2"].value, "GTO / 3011-9668 / Pvtenis")
        self.assertEqual(ws["C2"].value, "GTO / 3011-9668 / Pvtenis")
        self.assertEqual(ws["D2"].value, "ClubGTO")

        zelle_ws = wb["Zelle"]
        self.assertEqual(zelle_ws.max_row, 1)

    @patch("api.audit_export._fetch_stripe_rows", return_value=[])
    @patch("api.audit_export._club_name_map", return_value={})
    def test_build_audit_workbook_writes_manual_group_and_club_columns(
        self,
        _club_map,
        _stripe,
    ):
        manual_rows = [
            ManualAuditRow(
                amount_usd=199.99,
                payer_name="MR ROHIT KOTHLAPURAM",
                group_title="GTO / 3011-9668 / Pvtenis",
                club_label="ClubGTO",
                time_label="June 21, 2026 at 11:57 PM",
            )
        ]

        def manual_side_effect(session, payment_cls, build_read, club_names, from_dt, to_dt):
            if payment_cls.__name__ == "ZellePayment":
                return manual_rows
            return []

        with patch(
            "api.audit_export._fetch_manual_rows",
            side_effect=manual_side_effect,
        ):
            content = build_audit_workbook(
                MagicMock(),
                datetime(2026, 6, 21, 4, 0, tzinfo=timezone.utc),
                datetime(2026, 6, 22, 3, 59, 59, 999999, tzinfo=timezone.utc),
            )

        wb = load_workbook(io.BytesIO(content))
        zelle_ws = wb["Zelle"]
        self.assertEqual(
            [cell.value for cell in zelle_ws[1]],
            ["Amount", "Name", "Group", "Club", "Time"],
        )
        self.assertEqual(zelle_ws["A2"].value, 199.99)
        self.assertEqual(zelle_ws["B2"].value, "MR ROHIT KOTHLAPURAM")
        self.assertEqual(zelle_ws["C2"].value, "GTO / 3011-9668 / Pvtenis")
        self.assertEqual(zelle_ws["D2"].value, "ClubGTO")
        self.assertEqual(zelle_ws["E2"].value, "June 21, 2026 at 11:57 PM")


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
        self.assertEqual(to_dt, datetime(2026, 6, 20, 4, 59, 59, 999999, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
