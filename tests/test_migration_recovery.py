"""Tests for migrated supergroup recovery queue classification and finalize."""

import unittest
from unittest.mock import MagicMock, patch

from bot.services.migration_group_readd import ReaddGroupResult
from bot.services.migration_recovery import (
    build_readd_result_payload,
    classify_priority_tier,
    compute_priority_rank,
    map_readd_status,
)
from scripts.seed_migrated_group_recovery import build_seed_candidates
from scripts.migrated_groups_activity_report import GroupAgg, MigratedGroupRow


class TestClassifyPriorityTier(unittest.TestCase):
    def test_deposit_beats_active(self) -> None:
        self.assertEqual(
            classify_priority_tier(deposit_cents=100, active_in_past_30_days=True),
            1,
        )

    def test_active_without_deposit(self) -> None:
        self.assertEqual(
            classify_priority_tier(deposit_cents=0, active_in_past_30_days=True),
            2,
        )

    def test_rest(self) -> None:
        self.assertEqual(
            classify_priority_tier(deposit_cents=0, active_in_past_30_days=False),
            3,
        )


class TestComputePriorityRank(unittest.TestCase):
    def test_deposit_rank_orders_higher_deposits_first(self) -> None:
        high = compute_priority_rank(
            priority_tier=1,
            deposit_cents=500_000,
            last_activity_epoch=0,
            telegram_chat_id=-1001,
            sequence=0,
        )
        low = compute_priority_rank(
            priority_tier=1,
            deposit_cents=10_000,
            last_activity_epoch=0,
            telegram_chat_id=-1002,
            sequence=0,
        )
        self.assertLess(high, low)

    def test_same_tier_sequence_breaks_ties(self) -> None:
        first = compute_priority_rank(
            priority_tier=2,
            deposit_cents=0,
            last_activity_epoch=1_700_000_000,
            telegram_chat_id=-1001,
            sequence=0,
        )
        second = compute_priority_rank(
            priority_tier=2,
            deposit_cents=0,
            last_activity_epoch=1_700_000_000,
            telegram_chat_id=-1002,
            sequence=1,
        )
        self.assertLess(first, second)


class TestMapReaddStatus(unittest.TestCase):
    def _base_result(self, **kwargs) -> ReaddGroupResult:
        defaults = dict(
            chat_id=-1001,
            club_id=2,
            club_key="round_table",
            title="RT / / player",
            member_count_before=1,
            member_count_after=2,
            status="ok",
        )
        defaults.update(kwargs)
        return ReaddGroupResult(**defaults)

    def test_all_already_member_is_complete(self) -> None:
        result = self._base_result(
            already_member=["player:123", "staff:@RoundTableSupport3"],
            status="ok",
        )
        status, err = map_readd_status(result)
        self.assertEqual(status, "complete")
        self.assertIsNone(err)

    def test_privacy_blocked(self) -> None:
        result = self._base_result(
            privacy_blocked=["player:123"],
            status="privacy_fallback",
        )
        status, err = map_readd_status(result)
        self.assertEqual(status, "privacy_blocked")
        self.assertIsNone(err)

    def test_failed_entries(self) -> None:
        result = self._base_result(
            failed=["staff:@bot:SomeError"],
            status="partial",
        )
        status, err = map_readd_status(result)
        self.assertEqual(status, "failed")
        self.assertIn("staff:@bot", err or "")

    def test_no_targets_skipped(self) -> None:
        result = self._base_result(status="no_targets")
        status, _ = map_readd_status(result)
        self.assertEqual(status, "skipped")

    def test_build_payload(self) -> None:
        result = self._base_result(added=["player:1"], status="ok")
        payload = build_readd_result_payload(result)
        self.assertEqual(payload["inner_status"], "ok")
        self.assertEqual(payload["added"], ["player:1"])


class TestBuildSeedCandidates(unittest.TestCase):
    def test_priority_ordering(self) -> None:
        groups = [
            MigratedGroupRow(
                club_id=2,
                club_key="round_table",
                group_title="rest",
                old_chat_id=-1,
                current_chat_id=-1001,
            ),
            MigratedGroupRow(
                club_id=2,
                club_key="round_table",
                group_title="active",
                old_chat_id=-2,
                current_chat_id=-1002,
            ),
            MigratedGroupRow(
                club_id=2,
                club_key="round_table",
                group_title="deposit",
                old_chat_id=-3,
                current_chat_id=-1003,
            ),
        ]
        deposit_by_chat = {-1003: 50_000}
        active_agg = GroupAgg()
        active_agg.touch("payment", None)
        activity_by_chat = {-1002: active_agg}

        with patch(
            "scripts.seed_migrated_group_recovery.load_player_rows_by_chat",
            return_value={},
        ), patch(
            "scripts.seed_migrated_group_recovery.load_player_display_names_by_chat",
            return_value={},
        ):
            candidates = build_seed_candidates(
                groups,
                deposit_by_chat=deposit_by_chat,
                activity_by_chat=activity_by_chat,
            )

        tiers = [c.priority_tier for c in candidates]
        self.assertEqual(tiers, [1, 2, 3])
        self.assertEqual(candidates[0].telegram_chat_id, -1003)
        self.assertEqual(candidates[1].telegram_chat_id, -1002)
        self.assertEqual(candidates[2].telegram_chat_id, -1001)
        self.assertLess(candidates[0].priority_tier, candidates[1].priority_tier)
        self.assertLess(candidates[1].priority_tier, candidates[2].priority_tier)


if __name__ == "__main__":
    unittest.main()
