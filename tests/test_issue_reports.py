"""Unit tests for issue report service."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.issue_reports import (
    ISSUE_REPORT_TAGS,
    IssueReportFileInput,
    IssueReportValidationError,
    create_issue_report,
    format_issue_report_slack_body,
    normalize_tags,
    validate_files,
)
from db.models import IssueReport, IssueReportAttachment


class TestNormalizeTags(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(normalize_tags(None), [])
        self.assertEqual(normalize_tags([]), [])

    def test_dedupes_and_splits(self) -> None:
        self.assertEqual(
            normalize_tags(["cashout,deposit", "cashout"]),
            ["cashout", "deposit"],
        )

    def test_rejects_invalid(self) -> None:
        with self.assertRaises(IssueReportValidationError):
            normalize_tags(["not_a_tag"])


class TestValidateFiles(unittest.TestCase):
    def test_rejects_too_many(self) -> None:
        files = [
            IssueReportFileInput("a.png", "image/png", b"x")
            for _ in range(6)
        ]
        with self.assertRaises(IssueReportValidationError):
            validate_files(files)

    def test_rejects_bad_type(self) -> None:
        with self.assertRaises(IssueReportValidationError):
            validate_files(
                [IssueReportFileInput("a.pdf", "application/pdf", b"x")]
            )

    def test_rejects_oversized(self) -> None:
        with self.assertRaises(IssueReportValidationError):
            validate_files(
                [
                    IssueReportFileInput(
                        "big.png",
                        "image/png",
                        b"x" * (5 * 1024 * 1024 + 1),
                    )
                ]
            )


class TestFormatIssueReportSlackBody(unittest.TestCase):
    def test_includes_fields(self) -> None:
        report = IssueReport(
            id=7,
            title="Broken deposit",
            description="Nothing happens",
            tags=["deposit", "bot_issue"],
            reporter_name="Alice",
            reporter_source="api",
        )
        body = format_issue_report_slack_body(report)
        self.assertIn("Ticket: #7", body)
        self.assertIn("Title: Broken deposit", body)
        self.assertIn("Reporter: Alice", body)
        self.assertIn("Tags: deposit, bot_issue", body)
        self.assertIn("Nothing happens", body)


class TestCreateIssueReport(unittest.IsolatedAsyncioTestCase):
    async def test_creates_report_and_calls_slack(self) -> None:
        db = MagicMock()
        db.flush = MagicMock()
        db.refresh = MagicMock()

        captured: dict = {}

        def add_side_effect(obj):
            if isinstance(obj, IssueReport):
                obj.id = 42
                obj.created_at = datetime.now(timezone.utc)
                captured["report"] = obj
            elif isinstance(obj, IssueReportAttachment):
                obj.id = 1
                captured.setdefault("attachments", []).append(obj)

        db.add.side_effect = add_side_effect

        slack_mock = AsyncMock(
            return_value=(True, "1234.5678", ["F001"]),
        )

        with patch(
            "bot.services.slack_ops_notify.notify_slack_issue_report",
            slack_mock,
        ):
            report = await create_issue_report(
                db,
                title="Title",
                description="Details",
                tags=["cashout"],
                reporter_name="Bob",
                reporter_source="api",
                files=[
                    IssueReportFileInput("shot.png", "image/png", b"png-bytes"),
                ],
            )

        self.assertEqual(report.id, 42)
        self.assertEqual(report.slack_message_ts, "1234.5678")
        slack_mock.assert_awaited_once()
        call_kwargs = slack_mock.await_args.kwargs
        self.assertEqual(call_kwargs["tags"], ["cashout"])
        self.assertEqual(call_kwargs["file_bytes"][0][0], "shot.png")

    async def test_requires_title_and_description(self) -> None:
        db = MagicMock()
        with self.assertRaises(IssueReportValidationError):
            await create_issue_report(db, title=" ", description="x")
        with self.assertRaises(IssueReportValidationError):
            await create_issue_report(db, title="x", description=" ")


class TestIssueReportTags(unittest.TestCase):
    def test_allowlist(self) -> None:
        self.assertEqual(
            ISSUE_REPORT_TAGS,
            frozenset({"bot_issue", "cashout", "deposit", "rakeback"}),
        )


if __name__ == "__main__":
    unittest.main()
