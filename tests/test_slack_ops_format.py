"""Tests for Slack ops message beautification."""

from __future__ import annotations

import unittest

from bot.services.slack_ops_format import beautify_slack_body


class TestBeautifyMigrationRecovery(unittest.TestCase):
    def test_failure_alert(self) -> None:
        body = (
            "Issue: failed\n"
            "failed\n"
            "Failures: player:7784197742:ValueError: entity missing\n"
            "GC: GTO / 7546-1538 / johnny\n"
            "chat_id=-1003734624292\n"
            "club=clubgto"
        )
        text = beautify_slack_body(body, source="migration_recovery")
        self.assertIn("*Failed*", text)
        self.assertIn("`GTO / 7546-1538 / johnny`", text)
        self.assertIn("7784197742", text)
        self.assertNotIn("Issue: failed", text)

    def test_progress_summary(self) -> None:
        body = (
            "Migration recovery progress (tier 1+2)\n\n"
            "Creator Club\n"
            "  in group: 85% (85/100) | queue left: 40 | queue done: 60% (60/100)\n"
            "  in group pending queue: 13\n"
            "  direct added: 10 | joined via link: 45 | still missing: 5"
        )
        text = beautify_slack_body(body, source="migration_recovery")
        self.assertIn("*Progress*", text)
        self.assertIn("*Creator Club*", text)
        self.assertIn("direct added", text)


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
