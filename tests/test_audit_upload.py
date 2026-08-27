"""API tests for audit trade record upload-all."""

from __future__ import annotations

import os
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import create_token, get_current_admin
from api.routes.audit import router
from db.connection import get_db_dependency
from db.models import Club, TradeRecordLine, TradeRecordUpload
from tests.fixtures.trade_record_xlsx import build_sample_trade_record_xlsx

TOKEN = create_token()


class AuditUploadAllApiTestCase(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {"DASHBOARD_PASSWORD": "changeme"}, clear=False)
        self.env_patch.start()
        self.mock_db = MagicMock()
        self.upload_rows: list[TradeRecordUpload] = []
        self.line_rows: list[TradeRecordLine] = []
        self._upload_id = 0

        club = Club(id=2, name="Round Table", telegram_user_id=1)

        def query_model(model):
            q = MagicMock()

            if model is Club:
                q.filter.return_value.params.return_value.first.return_value = club
                q.filter.return_value.first.return_value = club
                return q

            if model is TradeRecordUpload:
                def filter_by(**kwargs):
                    q = MagicMock()
                    q.first.return_value = self._existing_upload(**kwargs)
                    return q

                q.filter_by.side_effect = filter_by
                return q

            if model is TradeRecordLine:
                q.filter_by.return_value.delete.return_value = None
                return q

            return q

        self.mock_db.query.side_effect = query_model

        def add(obj):
            if isinstance(obj, TradeRecordUpload):
                self._upload_id += 1
                obj.id = self._upload_id
                self.upload_rows.append(obj)
            elif isinstance(obj, TradeRecordLine):
                self.line_rows.append(obj)

        self.mock_db.add.side_effect = add
        self.mock_db.flush = MagicMock()
        self.mock_db.commit = MagicMock()
        self.mock_db.refresh = MagicMock()

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_admin] = lambda: "admin"

        mock_db = self.mock_db

        def override_db():
            yield mock_db

        app.dependency_overrides[get_db_dependency] = override_db
        self.client = TestClient(app)

    def tearDown(self):
        self.env_patch.stop()

    def _existing_upload(self, **kwargs):
        for row in self.upload_rows:
            if kwargs.get("club_slug") is not None and row.club_slug == kwargs.get(
                "club_slug"
            ):
                if row.audit_date == kwargs.get("audit_date"):
                    return row
            if kwargs.get("club_id") is not None and row.club_id == kwargs.get(
                "club_id"
            ):
                if row.audit_date == kwargs.get("audit_date"):
                    if kwargs.get("club_slug") is None or row.club_slug in (
                        None,
                        kwargs.get("club_slug"),
                    ):
                        return row
        return None

    @patch("api.routes.audit.sync_identities")
    @patch("api.routes.audit.resolve_club_id", return_value=2)
    def test_upload_all_success(self, _resolve, mock_sync):
        from api.trade_record_sync import IdentitySyncReport

        mock_sync.return_value = IdentitySyncReport(
            identities_extracted=3,
            postgres_inserted=2,
            postgres_updated=1,
            gg_computer_upserted=3,
        )
        audit_date = date(2026, 6, 21)
        files = [
            (
                "RT-21.xlsx",
                build_sample_trade_record_xlsx(
                    club_label="Round Table",
                    audit_date=audit_date,
                    period_tz="UTC-4:00",
                ),
            ),
            (
                "AT-21.xlsx",
                build_sample_trade_record_xlsx(club_label="Aces Table", audit_date=audit_date),
            ),
            (
                "GTO-21.xlsx",
                build_sample_trade_record_xlsx(club_label="ClubGTO", audit_date=audit_date),
            ),
            (
                "CC-21.xlsx",
                build_sample_trade_record_xlsx(club_label="Creator Club", audit_date=audit_date),
            ),
        ]
        response = self.client.post(
            "/api/audit/trade-records/upload-all",
            headers={"Authorization": f"Bearer {TOKEN}"},
            files=[
                (
                    "files",
                    (
                        name,
                        raw,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                )
                for name, raw in files
            ],
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body), 4)
        slugs = {row["club_slug"] for row in body}
        self.assertEqual(
            slugs,
            {"round-table", "aces-table", "clubgto", "creator-club"},
        )
        self.assertTrue(all(row["audit_date"] == "2026-06-21" for row in body))

    def test_upload_all_requires_auth(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        response = client.post(
            "/api/audit/trade-records/upload-all",
            files={"files": ("x.xlsx", b"bad", "application/octet-stream")},
        )
        self.assertIn(response.status_code, (401, 403))

    @patch("api.routes.audit.resolve_club_id", return_value=2)
    def test_upload_all_rejects_wrong_file_count(self, _resolve):
        raw = build_sample_trade_record_xlsx(
            club_label="Round Table",
            audit_date=date(2026, 6, 21),
        )
        response = self.client.post(
            "/api/audit/trade-records/upload-all",
            headers={"Authorization": f"Bearer {TOKEN}"},
            files=[
                (
                    "files",
                    (
                        "RT-21.xlsx",
                        raw,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                )
            ],
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("4", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
