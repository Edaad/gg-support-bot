"""Tests for issue report resolution and reminders."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.issue_reports import (
    REMINDER_INTERVAL_HOURS,
    IssueReportValidationError,
    list_open_reports_needing_reminder,
    resolve_report,
)
from db.models import IssueReport


class TestResolveReport(unittest.IsolatedAsyncioTestCase):
    async def test_requires_resolution_notes(self) -> None:
        db = MagicMock()
        report = IssueReport(id=1, title="T", status="open", description="d")
        with patch(
            "bot.services.issue_reports.get_issue_report",
            return_value=report,
        ):
            with self.assertRaises(IssueReportValidationError):
                await resolve_report(
                    db,
                    1,
                    resolved_by_telegram_user_id=99,
                    resolution_notes=" ",
                )

    async def test_resolves_and_notifies_slack_thread(self) -> None:
        db = MagicMock()
        db.flush = MagicMock()
        db.refresh = MagicMock()
        report = IssueReport(
            id=5,
            title="Broken bot",
            status="open",
            description="details",
            slack_message_ts="111.222",
            notify_tags=["engineer"],
        )
        slack_mock = AsyncMock(return_value=True)
        with patch("bot.services.issue_reports.get_issue_report", return_value=report):
            with patch(
                "bot.services.slack_ops_notify.notify_slack_issue_report_thread",
                slack_mock,
            ):
                result = await resolve_report(
                    db,
                    5,
                    resolved_by_telegram_user_id=42,
                    resolution_notes="Restarted worker",
                )
        self.assertFalse(result.already_resolved)
        self.assertEqual(report.status, "resolved")
        self.assertEqual(report.resolution_notes, "Restarted worker")
        slack_mock.assert_awaited_once()


class TestReminderDue(unittest.TestCase):
    def test_open_report_due_after_interval(self) -> None:
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=REMINDER_INTERVAL_HOURS, minutes=5)
        report = IssueReport(
            id=1,
            title="Open",
            status="open",
            description="x",
            created_at=old,
            last_slack_reminder_at=None,
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            report
        ]
        due = list_open_reports_needing_reminder(db)
        self.assertEqual(len(due), 1)

    def test_recently_reminded_not_due(self) -> None:
        now = datetime.now(timezone.utc)
        report = IssueReport(
            id=2,
            title="Open",
            status="open",
            description="x",
            created_at=now - timedelta(hours=10),
            last_slack_reminder_at=now - timedelta(minutes=30),
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            report
        ]
        due = list_open_reports_needing_reminder(db)
        self.assertEqual(due, [])


if __name__ == "__main__":
    unittest.main()
