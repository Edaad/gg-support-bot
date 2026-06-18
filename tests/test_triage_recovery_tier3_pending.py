"""Tests for tier-3 pending recovery triage helpers."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from scripts.migrated_groups_activity_report import GroupAgg
from scripts.triage_recovery_tier3_pending import (
    RecoveryRowForTriage,
    build_triage_csv_row,
    classify_triage_action,
    finalize_triage_decision,
)


def _sample_row(**kwargs) -> RecoveryRowForTriage:
    defaults = dict(
        row_id=42,
        club_key="round_table",
        club_id=1,
        group_title="RT / 123 / player",
        telegram_chat_id=-100123,
        old_chat_id=-123,
        player_telegram_user_id=999,
        player_username="@player",
        priority_tier=3,
        priority_rank=30_000_000_000,
        readd_status="pending",
    )
    defaults.update(kwargs)
    return RecoveryRowForTriage(**defaults)


class TestClassifyTriageAction(unittest.TestCase):
    def test_active_with_deposits_promotes_tier1(self) -> None:
        agg = GroupAgg()
        agg.add_payment(5000, datetime(2026, 1, 1, tzinfo=timezone.utc))
        agg.touch("stripe", datetime(2026, 1, 1, tzinfo=timezone.utc))

        decision = classify_triage_action(agg=agg, account_check="skipped_active")
        self.assertEqual(decision.action, "promote")
        self.assertEqual(decision.new_tier, 1)
        self.assertEqual(decision.deposit_cents, 5000)

    def test_active_without_deposits_promotes_tier2(self) -> None:
        agg = GroupAgg()
        agg.touch("player_activity", datetime(2026, 2, 1, tzinfo=timezone.utc))

        decision = classify_triage_action(agg=agg, account_check="skipped_active")
        self.assertEqual(decision.action, "promote")
        self.assertEqual(decision.new_tier, 2)
        self.assertEqual(decision.deposit_cents, 0)

    def test_inactive_deleted_account_drops(self) -> None:
        decision = classify_triage_action(agg=None, account_check="deleted")
        self.assertEqual(decision.action, "drop_deleted")
        self.assertEqual(decision.last_error, "account_deleted")

    def test_inactive_not_found_drops(self) -> None:
        decision = classify_triage_action(agg=None, account_check="not_found")
        self.assertEqual(decision.action, "drop_deleted")

    def test_inactive_alive_drops_inactive(self) -> None:
        decision = classify_triage_action(agg=None, account_check="alive")
        self.assertEqual(decision.action, "drop_inactive")
        self.assertEqual(decision.last_error, "inactive_no_bot_activity")

    def test_inactive_uncheckable_drops_inactive(self) -> None:
        decision = classify_triage_action(agg=None, account_check="uncheckable")
        self.assertEqual(decision.action, "drop_inactive")


class TestFinalizeTriageDecision(unittest.TestCase):
    def test_promote_computes_rank(self) -> None:
        agg = GroupAgg()
        agg.touch("cashier", datetime(2026, 3, 1, tzinfo=timezone.utc))
        raw = classify_triage_action(agg=agg, account_check="skipped_active")
        final = finalize_triage_decision(
            raw,
            row_id=7,
            telegram_chat_id=-100555,
            old_tier=3,
            old_rank=30_000_000_000,
        )
        self.assertEqual(final.action, "promote")
        self.assertEqual(final.new_tier, 2)
        self.assertGreater(final.new_rank, 0)
        self.assertLess(final.new_rank, 30_000_000_000_000)

    def test_unchanged_when_tier_rank_match(self) -> None:
        agg = GroupAgg()
        agg.touch("bind_attempt", datetime(2026, 3, 1, tzinfo=timezone.utc))
        raw = classify_triage_action(agg=agg, account_check="skipped_active")
        rank = finalize_triage_decision(
            raw,
            row_id=1,
            telegram_chat_id=100,
            old_tier=3,
            old_rank=0,
        ).new_rank
        raw2 = classify_triage_action(agg=agg, account_check="skipped_active")
        final = finalize_triage_decision(
            raw2,
            row_id=1,
            telegram_chat_id=100,
            old_tier=2,
            old_rank=rank,
        )
        self.assertEqual(final.action, "unchanged")


class TestBuildTriageCsvRow(unittest.TestCase):
    def test_promote_csv_row(self) -> None:
        row = _sample_row()
        agg = GroupAgg()
        agg.touch("venmo", datetime(2026, 1, 15, tzinfo=timezone.utc))
        decision = finalize_triage_decision(
            classify_triage_action(agg=agg, account_check="skipped_active"),
            row_id=row.row_id,
            telegram_chat_id=row.telegram_chat_id,
            old_tier=3,
            old_rank=row.priority_rank,
        )
        csv_row = build_triage_csv_row(row, decision, agg=agg, apply=False)
        self.assertEqual(csv_row["action"], "promote")
        self.assertEqual(csv_row["new_tier"], 2)
        self.assertEqual(csv_row["would_apply"], "would")
        self.assertIn("venmo", csv_row["activity_signals"])

    def test_drop_csv_row(self) -> None:
        row = _sample_row()
        decision = classify_triage_action(agg=None, account_check="alive")
        csv_row = build_triage_csv_row(row, decision, agg=None, apply=True)
        self.assertEqual(csv_row["action"], "drop_inactive")
        self.assertEqual(csv_row["would_apply"], "yes")


if __name__ == "__main__":
    unittest.main()
