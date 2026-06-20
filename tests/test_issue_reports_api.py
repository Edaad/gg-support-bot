"""API tests for issue report routes."""

from __future__ import annotations

import io
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.issue_reports import router
from bot.services.issue_reports import IssueReportValidationError
from db.connection import get_db_dependency
from db.models import IssueReport, IssueReportAttachment


def _make_app(mock_db: MagicMock | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    db = mock_db or MagicMock()

    def override_db():
        yield db

    app.dependency_overrides[get_db_dependency] = override_db
    return app


def _sample_report() -> IssueReport:
    now = datetime.now(timezone.utc)
    report = IssueReport(
        id=1,
        title="Broken bot",
        description="Bot does not reply",
        tags=["bot_issue"],
        status="open",
        reporter_name="Alice",
        reporter_source="api",
        slack_message_ts="111.222",
        created_at=now,
        updated_at=now,
    )
    att = IssueReportAttachment(
        id=10,
        issue_report_id=1,
        filename="shot.png",
        content_type="image/png",
        content=b"png",
        slack_file_id="F123",
        created_at=now,
    )
    report.attachments = [att]
    return report


class IssueReportsApiTestCase(unittest.TestCase):
    def test_post_creates_report(self) -> None:
        report = _sample_report()
        with patch(
            "api.routes.issue_reports.create_issue_report",
            new=AsyncMock(return_value=report),
        ):
            client = TestClient(_make_app())
            response = client.post(
                "/api/issue-reports",
                data={
                    "title": "Broken bot",
                    "description": "Bot does not reply",
                    "tags": "bot_issue",
                    "reporter_name": "Alice",
                },
                files=[
                    (
                        "screenshots",
                        ("shot.png", io.BytesIO(b"png"), "image/png"),
                    ),
                ],
            )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["id"], 1)
        self.assertEqual(data["title"], "Broken bot")
        self.assertEqual(data["tags"], ["bot_issue"])
        self.assertEqual(len(data["attachments"]), 1)
        self.assertEqual(data["attachments"][0]["filename"], "shot.png")

    def test_post_validation_error(self) -> None:
        with patch(
            "api.routes.issue_reports.create_issue_report",
            new=AsyncMock(
                side_effect=IssueReportValidationError("title is required"),
            ),
        ):
            client = TestClient(_make_app())
            response = client.post(
                "/api/issue-reports",
                data={"title": "", "description": "x"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("title is required", response.json()["detail"])

    def test_list_reports(self) -> None:
        report = _sample_report()
        with patch(
            "api.routes.issue_reports.list_issue_reports",
            return_value=[report],
        ):
            client = TestClient(_make_app())
            response = client.get("/api/issue-reports")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], 1)

    def test_get_report_detail(self) -> None:
        report = _sample_report()
        with patch(
            "api.routes.issue_reports.get_issue_report",
            return_value=report,
        ):
            client = TestClient(_make_app())
            response = client.get("/api/issue-reports/1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "Broken bot")

    def test_get_report_not_found(self) -> None:
        with patch(
            "api.routes.issue_reports.get_issue_report",
            return_value=None,
        ):
            client = TestClient(_make_app())
            response = client.get("/api/issue-reports/999")

        self.assertEqual(response.status_code, 404)

    def test_get_attachment_bytes(self) -> None:
        att = IssueReportAttachment(
            id=10,
            issue_report_id=1,
            filename="shot.png",
            content_type="image/png",
            content=b"png-bytes",
        )
        with patch(
            "api.routes.issue_reports.get_issue_report_attachment",
            return_value=att,
        ):
            client = TestClient(_make_app())
            response = client.get("/api/issue-reports/1/attachments/10")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"png-bytes")
        self.assertEqual(response.headers["content-type"], "image/png")

    def test_get_attachment_not_found(self) -> None:
        with patch(
            "api.routes.issue_reports.get_issue_report_attachment",
            return_value=None,
        ):
            client = TestClient(_make_app())
            response = client.get("/api/issue-reports/1/attachments/10")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
