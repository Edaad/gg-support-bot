"""Tests for GTO weekly audit export."""

from __future__ import annotations

import io
import os
import unittest
import zipfile
from datetime import date, datetime, timedelta
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from api.audit_reconcile_export import MATCHING_HEADERS
from api.auth import create_token, get_current_admin
from api.gto_weekly_audit import (
    GtoWeeklyAuditError,
    build_gto_weekly_audit_workbook,
    date_from_filename,
    expected_week_dates,
    output_filename,
    parse_clubgto_rows,
    rail_bucket,
    validate_upload_set,
)
from api.routes.audit import router
from db.connection import get_db_dependency

TOKEN = create_token()
MONDAY = date(2026, 8, 10)


def _matching_xlsx(
    rows: list[tuple],
    *,
    sheet_name: str = "ClubGTO",
    headers: list[str] | None = None,
) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    use_headers = headers if headers is not None else list(MATCHING_HEADERS)
    for col, h in enumerate(use_headers, start=1):
        ws.cell(1, col, h)
    for r_idx, row in enumerate(rows, start=2):
        for c_idx, value in enumerate(row, start=1):
            ws.cell(r_idx, c_idx, value)
    # Extra club sheets so it looks like all-clubs export
    for title in ("Round Table", "Aces Table", "Creator Club"):
        if title not in wb.sheetnames:
            wb.create_sheet(title)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _week_files(
    monday: date = MONDAY,
    *,
    row_factory=None,
) -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    for i in range(7):
        day = monday + timedelta(days=i)
        if row_factory is None:
            rows = [
                (
                    datetime(day.year, day.month, day.day, 10, 0, 0),
                    "TrafficLight7",
                    -50.0,
                    "1111-0001",
                    "PlayerA",
                    "GTO Zelle",
                    "Jane Doe",
                    datetime(day.year, day.month, day.day, 9, 30, 0),
                    50.0,
                    "ClubGTO",
                )
            ]
        else:
            rows = row_factory(day, i)
        name = f"reconcile-all-clubs-{day.isoformat()}.xlsx"
        files.append((name, _matching_xlsx(rows)))
    return files


class GtoWeeklyAuditUnitTestCase(unittest.TestCase):
    def test_expected_week_dates_rejects_non_monday(self):
        with self.assertRaises(GtoWeeklyAuditError) as ctx:
            expected_week_dates(date(2026, 8, 11))
        self.assertIn("Monday", str(ctx.exception))

    def test_validate_upload_set_ok(self):
        names = [
            f"reconcile-all-clubs-{(MONDAY + timedelta(days=i)).isoformat()}.xlsx"
            for i in range(7)
        ]
        self.assertEqual(validate_upload_set(MONDAY, names), expected_week_dates(MONDAY))

    def test_validate_wrong_count(self):
        with self.assertRaises(GtoWeeklyAuditError) as ctx:
            validate_upload_set(MONDAY, ["reconcile-all-clubs-2026-08-10.xlsx"])
        self.assertIn("exactly 7", str(ctx.exception))

    def test_validate_bad_filename(self):
        with self.assertRaises(GtoWeeklyAuditError):
            date_from_filename("reconcile-all-clubs-2026-08-10 (1).xlsx")

    def test_validate_wrong_dates(self):
        names = [
            f"reconcile-all-clubs-{(date(2026, 8, 11) + timedelta(days=i)).isoformat()}.xlsx"
            for i in range(7)
        ]
        with self.assertRaises(GtoWeeklyAuditError) as ctx:
            validate_upload_set(MONDAY, names)
        self.assertIn("Missing", str(ctx.exception))

    def test_rail_bucket_rules(self):
        self.assertEqual(rail_bucket("GTO Zelle"), "zelle")
        self.assertEqual(rail_bucket("Vaughn venmo"), "venmo")
        self.assertEqual(rail_bucket("GTO Crypto"), "crypto")
        self.assertEqual(rail_bucket("Bonus"), "bonuses")
        self.assertEqual(rail_bucket("Admin Bonus Offer"), "bonuses")
        self.assertIsNone(rail_bucket("RT Zelle"))
        self.assertIsNone(rail_bucket("Cashout Zelle"))
        self.assertIsNone(rail_bucket("GTO Stripe"))
        self.assertIsNone(rail_bucket("rb"))

    def test_output_filename(self):
        self.assertEqual(output_filename(MONDAY), "GTO Audit Aug10_16-2026.xlsx")

    def test_parse_missing_sheet(self):
        wb = Workbook()
        wb.active.title = "Round Table"
        with self.assertRaises(GtoWeeklyAuditError) as ctx:
            parse_clubgto_rows(wb, filename="reconcile-all-clubs-2026-08-10.xlsx")
        self.assertIn("ClubGTO", str(ctx.exception))

    def test_parse_zero_rows(self):
        raw = _matching_xlsx([])
        wb = load_workbook(io.BytesIO(raw))
        with self.assertRaises(GtoWeeklyAuditError) as ctx:
            parse_clubgto_rows(wb, filename="reconcile-all-clubs-2026-08-10.xlsx")
        self.assertIn("no data rows", str(ctx.exception))

    def test_parse_missing_headers(self):
        raw = _matching_xlsx(
            [("x",)],
            headers=["Trade Time"],
        )
        wb = load_workbook(io.BytesIO(raw))
        with self.assertRaises(GtoWeeklyAuditError) as ctx:
            parse_clubgto_rows(wb, filename="reconcile-all-clubs-2026-08-10.xlsx")
        self.assertIn("missing required headers", str(ctx.exception))

    def test_build_workbook_processed_and_rails(self):
        def row_factory(day: date, i: int):
            base = datetime(day.year, day.month, day.day, 12, 0, 0)
            # Day 0: zelle + rt zelle + cashout + bonus + blank match venmo
            if i == 0:
                return [
                    (
                        base,
                        "Admin1",
                        -40.0,
                        "1000-0001",
                        "Alice",
                        "GTO Zelle",
                        "Alice Payer",
                        base.replace(hour=11),
                        40.0,
                        "ClubGTO",
                    ),
                    (
                        base.replace(minute=1),
                        "Admin1",
                        -30.0,
                        "1000-0002",
                        "Bob",
                        "RT Zelle",
                        "Bob Payer",
                        base.replace(hour=10),
                        30.0,
                        "Other",
                    ),
                    (
                        base.replace(minute=2),
                        "Admin1",
                        25.0,
                        "1000-0003",
                        "Cara",
                        "Cashout Zelle",
                        "Cara",
                        base.replace(hour=9),
                        25.0,
                        "ClubGTO",
                    ),
                    (
                        base.replace(minute=3),
                        "Admin1",
                        -5.0,
                        "1000-0004",
                        "Dan",
                        "Bonus",
                        "Dan Nick",
                        base.replace(hour=8),
                        5.0,
                        "",
                    ),
                    (
                        base.replace(minute=4),
                        "Admin1",
                        -20.0,
                        "1000-0005",
                        "Eve",
                        "Vaughn venmo",
                        "Eve Payer",
                        None,
                        None,
                        "@Janseashells",
                    ),
                ]
            if i == 1:
                return [
                    (
                        base,
                        "Admin2",
                        -100.0,
                        "2000-0001",
                        "Frank",
                        "GTO Crypto",
                        "bc1qabc…",
                        base.replace(hour=7),
                        100.0,
                        "BTC",
                    )
                ]
            return [
                (
                    base,
                    "AdminX",
                    -10.0,
                    f"3000-000{i}",
                    f"P{i}",
                    "GTO Stripe",
                    "",
                    None,
                    None,
                    "",
                )
            ]

        files = _week_files(MONDAY, row_factory=row_factory)
        content = build_gto_weekly_audit_workbook(MONDAY, files)
        wb = load_workbook(io.BytesIO(content))
        self.assertEqual(
            wb.sheetnames[:5],
            ["Processed", "Zelle", "Venmo", "Crypto", "Bonuses"],
        )

        processed = wb["Processed"]
        self.assertEqual(
            [processed.cell(1, c).value for c in range(1, 7)],
            [
                "Date / Time",
                "Admin Username",
                "Amount",
                "Player ID",
                "Player Username",
                "Category",
            ],
        )
        # 5 rows day0 + 1 day1 + 5 days stripe = 11
        data_rows = []
        for r in range(2, processed.max_row + 1):
            if processed.cell(r, 6).value:
                data_rows.append(
                    (
                        processed.cell(r, 2).value,
                        processed.cell(r, 4).value,
                        processed.cell(r, 6).value,
                    )
                )
        self.assertEqual(len(data_rows), 11)
        self.assertIn("ProcessedData", processed.tables)

        # Chronological: first trade times should be sorted
        times = [
            processed.cell(r, 1).value
            for r in range(2, processed.max_row + 1)
            if processed.cell(r, 1).value
        ]
        self.assertEqual(times, sorted(times))

        zelle = wb["Zelle"]
        # Only GTO Zelle (not RT, not cashout)
        zelle_names = [
            zelle.cell(r, 2).value
            for r in range(2, zelle.max_row)
            if zelle.cell(r, 2).value
        ]
        self.assertEqual(zelle_names, ["Alice Payer"])
        # Total row
        self.assertEqual(zelle.cell(3, 3).value, 40.0)

        venmo = wb["Venmo"]
        # Blank match still included
        self.assertEqual(venmo.cell(2, 2).value, "Eve Payer")
        self.assertIsNone(venmo.cell(2, 1).value)
        self.assertIsNone(venmo.cell(2, 3).value)
        self.assertEqual(venmo.cell(2, 4).value, "@Janseashells")
        self.assertEqual(venmo.cell(3, 3).value, 0)

        crypto = wb["Crypto"]
        self.assertEqual(crypto.cell(1, 2).value, "From")
        self.assertEqual(crypto.cell(2, 2).value, "bc1qabc…")
        self.assertEqual(crypto.cell(2, 4).value, "BTC")
        self.assertEqual(crypto.cell(3, 3).value, 100.0)

        bonuses = wb["Bonuses"]
        self.assertEqual(bonuses.cell(2, 2).value, "Dan Nick")
        self.assertEqual(bonuses.cell(3, 3).value, 5.0)

        with zipfile.ZipFile(io.BytesIO(content)) as z:
            pivot_parts = [n for n in z.namelist() if "pivotTables/" in n]
            self.assertTrue(pivot_parts, "expected pivot table part in output")


class GtoWeeklyAuditApiTestCase(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(
            os.environ, {"DASHBOARD_PASSWORD": "changeme"}, clear=False
        )
        self.env_patch.start()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_admin] = lambda: "admin"

        def override_db():
            yield None

        app.dependency_overrides[get_db_dependency] = override_db
        self.client = TestClient(app)

    def tearDown(self):
        self.env_patch.stop()

    def test_export_ok(self):
        files = _week_files(MONDAY)
        upload = [
            ("files", (name, raw, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
            for name, raw in files
        ]
        res = self.client.post(
            "/api/audit/gto-weekly-audit/export",
            data={"monday": MONDAY.isoformat()},
            files=upload,
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn(
            "GTO Audit Aug10_16-2026.xlsx",
            res.headers.get("content-disposition", ""),
        )
        wb = load_workbook(io.BytesIO(res.content))
        self.assertEqual(wb.sheetnames[0], "Processed")

    def test_export_validation_error(self):
        files = _week_files(MONDAY)[:3]
        upload = [
            ("files", (name, raw, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
            for name, raw in files
        ]
        res = self.client.post(
            "/api/audit/gto-weekly-audit/export",
            data={"monday": MONDAY.isoformat()},
            files=upload,
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("exactly 7", res.json()["detail"])


if __name__ == "__main__":
    unittest.main()
