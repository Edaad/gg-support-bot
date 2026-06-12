"""Tests for migration recovery batch quota drain and Slack stats."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.migration_group_readd import ReaddGroupResult
from bot.services.migration_recovery import (
    ClubRecoverySlackStats,
    RecoveryRow,
    classify_terminal_row_outcome,
    consumes_direct_add_quota,
    format_recovery_slack_summary,
    is_already_in_only_result,
    tick_async,
    was_direct_added,
)


class TestQuotaHelpers(unittest.TestCase):
    def _result(self, **kwargs) -> ReaddGroupResult:
        defaults = dict(
            chat_id=-1001,
            club_id=2,
            club_key="round_table",
            title="RT / / player",
            member_count_before=0,
            member_count_after=None,
            status="ok",
        )
        defaults.update(kwargs)
        return ReaddGroupResult(**defaults)

    def test_already_in_only_does_not_consume_quota(self) -> None:
        result = self._result(already_member=["player:@p1"])
        self.assertTrue(is_already_in_only_result(result))
        self.assertFalse(consumes_direct_add_quota(result))

    def test_direct_add_consumes_quota(self) -> None:
        result = self._result(added=["player:@p1"])
        self.assertTrue(consumes_direct_add_quota(result))

    def test_privacy_blocked_consumes_quota(self) -> None:
        result = self._result(
            privacy_blocked=["player:@p1"],
            status="privacy_fallback",
        )
        self.assertTrue(consumes_direct_add_quota(result))

    def test_no_targets_does_not_consume_quota(self) -> None:
        result = self._result(status="no_targets")
        self.assertFalse(consumes_direct_add_quota(result))

    def test_was_direct_added(self) -> None:
        self.assertTrue(
            was_direct_added({"added": ["player:@p1"], "already_member": []})
        )
        self.assertFalse(
            was_direct_added({"added": [], "already_member": ["player:@p1"]})
        )


class TestClassifyTerminalRowOutcome(unittest.TestCase):
    def test_direct_added_in_group(self) -> None:
        outcome = classify_terminal_row_outcome(
            readd_result={"added": ["player:1"]},
            player_in_group=True,
        )
        self.assertEqual(outcome, "direct_added")

    def test_invite_link_in_group_not_direct(self) -> None:
        outcome = classify_terminal_row_outcome(
            readd_result={"already_member": ["player:1"]},
            player_in_group=True,
        )
        self.assertEqual(outcome, "invite_link")

    def test_still_missing(self) -> None:
        outcome = classify_terminal_row_outcome(
            readd_result={"added": ["player:1"]},
            player_in_group=False,
        )
        self.assertEqual(outcome, "still_missing")


class TestFormatRecoverySlackSummary(unittest.TestCase):
    def test_includes_club_lines(self) -> None:
        text = format_recovery_slack_summary(
            [
                ClubRecoverySlackStats(
                    club_key="creator_club",
                    club_display_name="Creator Club",
                    total=100,
                    left=40,
                    done=60,
                    pct_done=60.0,
                    direct_added=10,
                    invite_link=45,
                    still_missing=5,
                )
            ]
        )
        self.assertIn("Creator Club", text)
        self.assertIn("left: 40", text)
        self.assertIn("60% (60/100)", text)
        self.assertIn("direct added: 10", text)
        self.assertIn("joined via link: 45", text)
        self.assertIn("still missing: 5", text)


class TestComputeRecoverySlackStats(unittest.IsolatedAsyncioTestCase):
    @patch(
        "bot.services.recovery_membership_check.mtproto_scan_recovery_rows",
        new_callable=AsyncMock,
    )
    @patch("db.connection.get_db")
    async def test_reads_rows_before_session_closes(
        self,
        mock_get_db: MagicMock,
        mock_scan: AsyncMock,
    ) -> None:
        from bot.services.migration_recovery import compute_recovery_slack_stats

        mock_row = MagicMock()
        mock_row.id = 1
        mock_row.club_key = "round_table"
        mock_row.readd_status = "pending"
        mock_row.telegram_chat_id = -1001
        mock_row.readd_result = None

        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = [mock_row]
        mock_get_db.return_value.__enter__.return_value = session

        stats = await compute_recovery_slack_stats()

        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0].club_key, "round_table")
        self.assertEqual(stats[0].total, 1)
        self.assertEqual(stats[0].left, 1)
        mock_scan.assert_not_called()


class TestTickAsyncQuotaDrain(unittest.IsolatedAsyncioTestCase):
    @patch("bot.services.migration_recovery._maybe_auto_disable_after_tick", new_callable=AsyncMock)
    @patch("bot.services.migration_recovery.set_flood_wait_policy")
    @patch("bot.services.migration_recovery.set_flood_wait_observer")
    @patch("bot.services.migration_recovery.get_migration_recovery_invite_delay_sec", return_value=0.0)
    @patch("bot.services.migration_recovery.get_migration_recovery_batch_size", return_value=2)
    @patch(
        "bot.services.migration_recovery.migration_recovery_active_club_keys",
        return_value=("creator_club",),
    )
    @patch("bot.services.migration_recovery.is_migration_recovery_enabled", return_value=True)
    @patch("bot.services.migration_recovery._process_row", new_callable=AsyncMock)
    @patch("bot.services.migration_recovery.claim_next_pending_row")
    async def test_drains_already_in_until_direct_add_quota(
        self,
        mock_claim: MagicMock,
        mock_process: AsyncMock,
        _mock_enabled: MagicMock,
        _mock_clubs: MagicMock,
        _mock_batch: MagicMock,
        _mock_delay: MagicMock,
        _mock_observer: MagicMock,
        _mock_policy: MagicMock,
        _mock_auto: AsyncMock,
    ) -> None:
        rows = [
            RecoveryRow(
                id=i,
                telegram_chat_id=-1000 - i,
                club_key="creator_club",
                club_id=3,
                group_title=f"CC {i}",
                old_chat_id=-500 - i,
                player_telegram_user_id=100 + i,
                player_username=f"p{i}",
            )
            for i in range(1, 5)
        ]
        mock_claim.side_effect = rows + [None]

        already = ReaddGroupResult(
            chat_id=-1001,
            club_id=3,
            club_key="creator_club",
            title="CC 1",
            member_count_before=0,
            member_count_after=None,
            status="ok",
            already_member=["player:@p1"],
        )
        added = ReaddGroupResult(
            chat_id=-1002,
            club_id=3,
            club_key="creator_club",
            title="CC 2",
            member_count_before=0,
            member_count_after=None,
            status="ok",
            added=["player:@p2"],
        )
        mock_process.side_effect = [
            ("complete", already),
            ("complete", already),
            ("complete", added),
            ("privacy_blocked", ReaddGroupResult(
                chat_id=-1004,
                club_id=3,
                club_key="creator_club",
                title="CC 4",
                member_count_before=0,
                member_count_after=None,
                status="privacy_fallback",
                privacy_blocked=["player:@p4"],
            )),
        ]

        summary = await tick_async()

        self.assertEqual(mock_claim.call_count, 4)
        self.assertEqual(summary["claimed"], 4)
        self.assertEqual(summary["already_skipped"], 2)
        self.assertEqual(summary["direct_add_quota_used"], 2)
        self.assertEqual(summary["complete"], 3)
        self.assertEqual(summary["privacy_blocked"], 1)


if __name__ == "__main__":
    unittest.main()
