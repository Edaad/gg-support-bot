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

    def test_progress_summary_includes_queue_snapshot(self) -> None:
        body = (
            "Migration recovery progress (tier 1+2)\n\n"
            "Creator Club\n"
            "  in group: 85% (85/100) | queue left: 40 | queue done: 60% (60/100)\n"
            "  direct added: 10 | joined via link: 45 | still missing: 5\n\n"
            "Queue snapshot (all tiers)\n\n"
            "Creator Club\n"
            "  tier 1+2 pending: 120 | tier 3 pending: 50 | processing: 2\n"
            "  skipped: 733 | failed: 8"
        )
        text = beautify_slack_body(body, source="migration_recovery")
        self.assertIn("*Queue snapshot*", text)
        self.assertIn("tier 1+2 pending: 120", text)
        self.assertIn("skipped: 733", text)


class TestBeautifyRecoveryTriage(unittest.TestCase):
    def test_apply_summary(self) -> None:
        body = (
            "Migration recovery triage — APPLY\n"
            "Total rows: 1032\n"
            "  promote: 258\n"
            "  drop_inactive: 733\n"
            "  drop_deleted: 33\n"
            "  repair_pending: 8\n"
            "  round_table: promote=200 repair=5 drop_inactive=500 drop_deleted=14\n\n"
            "DB apply results:\n"
            "  promoted: 258\n"
            "  drop_inactive: 733\n\n"
            "Output CSV: backups/recovery_tier3_triage_round_table.csv"
        )
        text = beautify_slack_body(body, source="recovery_triage")
        self.assertIn("*Recovery triage* — APPLY", text)
        self.assertIn("promote: 258", text)
        self.assertIn("*Database*", text)
        self.assertIn("promoted: 258", text)
        self.assertIn("`backups/recovery_tier3_triage_round_table.csv`", text)


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
