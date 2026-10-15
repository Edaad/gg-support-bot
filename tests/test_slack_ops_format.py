"""Tests for Slack ops message beautification."""

from __future__ import annotations

import unittest

from bot.services.slack_ops_format import beautify_slack_body


class TestBeautifyIssueReport(unittest.TestCase):
    def test_notify_and_details(self) -> None:
        body = (
            "Issue report\n\n"
            "Ticket: #9\n"
            "Title: Bot down\n"
            "Notify: Engineer\n"
            "Reporter: Sam (source=telegram)\n\n"
            "Details:\n"
            "Deposit flow broken"
        )
        text = beautify_slack_body(body, source="issue_report")
        self.assertIn("*For:* Engineer", text)
        self.assertIn("*Details:*", text)
        self.assertIn("Deposit flow broken", text)
        self.assertNotIn("*Reporter:*", text)


class TestBeautifyNotificationReport(unittest.TestCase):
    def test_bug_report(self) -> None:
        body = (
            "Notification bug report\n\n"
            "Reporter: @ClubGTO (id=7516419496)\n"
            "Notification chat_id=-5273879167 message_id=7827\n\n"
            "Original notification:\n"
            "---\n"
            "Name: Jesus Juarez\n"
            "Amount: $\n"
            "---\n\n"
            "Reason:\n"
            "No amount"
        )
        text = beautify_slack_body(body, source="notification_report")
        self.assertIn("*Bug report*", text)
        self.assertIn("@ClubGTO", text)
        self.assertIn("*Reason:* No amount", text)
        self.assertIn("Jesus Juarez", text)


if __name__ == "__main__":
    unittest.main()
