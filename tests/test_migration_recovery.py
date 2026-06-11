"""Tests for migrated supergroup recovery queue classification and finalize."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.migration_recovery_priority import (
    classify_priority_tier,
    compute_priority_rank,
)
from bot.services.migration_recovery import (
    RECOVERY_CLUB_KEYS,
    build_readd_result_payload,
    claim_pending_batch,
    format_auto_disable_notification,
    format_readd_admin_notification,
    format_rate_limit_admin_notification,
    map_readd_status,
    peek_next_recovery_rows,
    release_processing_rows,
    should_notify_rt_ops,
    _handle_rate_limit_abort,
    _maybe_auto_disable_after_tick,
)
from bot.services.migration_group_readd import FloodWaitAbortError, ReaddGroupResult
from notification.formatting import format_group_chat_line
from bot.services.migration_recovery import RecoveryRow
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


class TestShouldNotifyRtOps(unittest.TestCase):
    def _result(self, **kwargs) -> ReaddGroupResult:
        defaults = dict(
            chat_id=-1001,
            club_id=2,
            club_key="round_table",
            title="RT / / player",
            member_count_before=0,
            member_count_after=None,
            status="error",
        )
        defaults.update(kwargs)
        return ReaddGroupResult(**defaults)

    def test_failed_status_notifies(self) -> None:
        result = self._result(status="error", error="listener_not_connected")
        self.assertTrue(should_notify_rt_ops("failed", result))

    def test_complete_does_not_notify(self) -> None:
        result = self._result(status="ok", added=["player:1"])
        self.assertFalse(should_notify_rt_ops("complete", result))

    def test_rate_limit_marker_in_error_blob(self) -> None:
        result = self._result(status="ok")
        self.assertTrue(
            should_notify_rt_ops(
                "complete",
                result,
                pre_error="FloodWaitError: wait 90 seconds",
            )
        )


class TestFormatReaddAdminNotification(unittest.TestCase):
    def test_includes_gc_and_added_accounts(self) -> None:
        row = RecoveryRow(
            id=1,
            telegram_chat_id=-100123,
            club_key="round_table",
            club_id=2,
            group_title="RT / / @player1",
            old_chat_id=-456,
            player_telegram_user_id=111,
            player_username="player1",
        )
        result = ReaddGroupResult(
            chat_id=-100123,
            club_id=2,
            club_key="round_table",
            title="RT / / @player1",
            member_count_before=2,
            member_count_after=4,
            status="ok",
            added=["player:@player1", "staff:@RoundTableSupport3"],
            already_member=["bot:@YTranslateBot"],
        )
        text = format_readd_admin_notification(
            row=row,
            result=result,
            terminal_status="complete",
            club_display_name="Round Table",
            gc_line=format_group_chat_line(
                group_title=row.group_title,
                telegram_chat_id=row.telegram_chat_id,
            ),
        )
        self.assertIn('<a href="https://t.me/c/123">', text)
        self.assertIn("RT / / @player1", text)
        self.assertIn("chat_id=-100123", text)
        self.assertIn("@player1", text)
        self.assertIn("@RoundTableSupport3", text)
        self.assertIn("@YTranslateBot", text)
        self.assertIn("Result: complete", text)

    def test_escapes_special_chars_in_title(self) -> None:
        row = RecoveryRow(
            id=1,
            telegram_chat_id=-100123,
            club_key="round_table",
            club_id=2,
            group_title="RT & <test>",
            old_chat_id=-456,
            player_telegram_user_id=None,
            player_username=None,
        )
        result = ReaddGroupResult(
            chat_id=-100123,
            club_id=2,
            club_key="round_table",
            title="RT & <test>",
            member_count_before=0,
            member_count_after=None,
            status="ok",
        )
        text = format_readd_admin_notification(
            row=row,
            result=result,
            terminal_status="complete",
            club_display_name="Round Table",
            gc_line='Group Chat: <a href="https://t.me/c/123">RT &amp; &lt;test&gt;</a>',
        )
        self.assertIn("RT &amp; &lt;test&gt;", text)
        self.assertNotIn("RT & <test>", text)


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


class TestFormatAutoDisableNotification(unittest.TestCase):
    def test_club_exhausted_message(self) -> None:
        text = format_auto_disable_notification(
            reason="club_exhausted",
            exhausted_club_key="clubgto",
            pending_snapshot={
                "round_table": 1200,
                "creator_club": 400,
                "clubgto": 0,
            },
        )
        self.assertIn("club_exhausted", text)
        self.assertIn("clubgto", text)
        self.assertIn("round_table: 1200", text)
        self.assertIn("GC_MIGRATION_RECOVERY_ENABLED", text)

    def test_all_clubs_drained_message(self) -> None:
        text = format_auto_disable_notification(
            reason="all_clubs_drained",
            exhausted_club_key="round_table",
            pending_snapshot={
                "round_table": 0,
                "creator_club": 0,
                "clubgto": 0,
            },
        )
        self.assertIn("All clubs drained", text)


class TestMaybeAutoDisableAfterTick(unittest.TestCase):
    def test_triggers_when_one_club_empty(self) -> None:
        with patch(
            "bot.services.migration_recovery.pending_count_by_club",
            return_value={
                "round_table": 10,
                "creator_club": 0,
                "clubgto": 5,
            },
        ), patch(
            "bot.services.migration_recovery.auto_disable_migration_recovery",
            new_callable=AsyncMock,
        ) as mock_disable:
            asyncio.run(_maybe_auto_disable_after_tick())
        mock_disable.assert_awaited_once_with(
            reason="club_exhausted",
            exhausted_club_key="creator_club",
            pending_snapshot={
                "round_table": 10,
                "creator_club": 0,
                "clubgto": 5,
            },
        )

    def test_all_drained_reason(self) -> None:
        with patch(
            "bot.services.migration_recovery.pending_count_by_club",
            return_value={
                "round_table": 0,
                "creator_club": 0,
                "clubgto": 0,
            },
        ), patch(
            "bot.services.migration_recovery.auto_disable_migration_recovery",
            new_callable=AsyncMock,
        ) as mock_disable:
            asyncio.run(_maybe_auto_disable_after_tick())
        mock_disable.assert_awaited_once_with(
            reason="all_clubs_drained",
            exhausted_club_key="round_table",
            pending_snapshot={
                "round_table": 0,
                "creator_club": 0,
                "clubgto": 0,
            },
        )

    def test_no_trigger_when_all_clubs_have_queue(self) -> None:
        with patch(
            "bot.services.migration_recovery.pending_count_by_club",
            return_value={
                "round_table": 1,
                "creator_club": 2,
                "clubgto": 3,
            },
        ), patch(
            "bot.services.migration_recovery.auto_disable_migration_recovery",
            new_callable=AsyncMock,
        ) as mock_disable:
            asyncio.run(_maybe_auto_disable_after_tick())
        mock_disable.assert_not_awaited()


class TestClaimPendingBatchPerClub(unittest.TestCase):
    def _make_db_row(self, **kwargs):
        row = MagicMock()
        defaults = {
            "id": 1,
            "telegram_chat_id": -1001,
            "club_key": "round_table",
            "club_id": 2,
            "group_title": "RT test",
            "old_chat_id": -501,
            "player_telegram_user_id": None,
            "player_username": None,
            "priority_tier": 1,
            "priority_rank": 10,
        }
        defaults.update(kwargs)
        for key, value in defaults.items():
            setattr(row, key, value)
        return row

    @patch("bot.services.migration_recovery.get_migration_recovery_batch_size", return_value=1)
    @patch("db.connection.get_db")
    def test_claims_up_to_batch_size_per_club(
        self, mock_get_db: MagicMock, _mock_batch_size: MagicMock
    ) -> None:
        rt_row = self._make_db_row(id=1, club_key="round_table", priority_rank=1)
        cc_row = self._make_db_row(
            id=2, club_key="creator_club", club_id=3, priority_rank=2
        )
        gto_row = self._make_db_row(
            id=3, club_key="clubgto", club_id=4, priority_rank=3
        )

        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session

        execute_results = {
            "round_table": MagicMock(scalars=MagicMock(return_value=MagicMock(all=lambda: [1]))),
            "creator_club": MagicMock(scalars=MagicMock(return_value=MagicMock(all=lambda: [2]))),
            "clubgto": MagicMock(scalars=MagicMock(return_value=MagicMock(all=lambda: [3]))),
        }

        def fake_execute(stmt):
            club_key = None
            for club in RECOVERY_CLUB_KEYS:
                if club in str(stmt):
                    club_key = club
                    break
            return execute_results[club_key or "round_table"]

        session.execute.side_effect = fake_execute
        session.get.side_effect = lambda _model, row_id: {
            1: rt_row,
            2: cc_row,
            3: gto_row,
        }[int(row_id)]

        query = MagicMock()
        session.query.return_value = query
        query.filter.return_value = query
        query.all.return_value = [gto_row, cc_row, rt_row]

        rows = claim_pending_batch()

        self.assertEqual(len(rows), 3)
        self.assertEqual([r.club_key for r in rows], list(RECOVERY_CLUB_KEYS))
        self.assertEqual(session.execute.call_count, len(RECOVERY_CLUB_KEYS))


class TestIsMigrationRecoveryEnabledWithDbFlag(unittest.TestCase):
    @patch("club_gc_settings.is_dm_gc_listener_enabled", return_value=True)
    @patch("club_gc_settings._env_bool", return_value=True)
    @patch(
        "bot.services.migration_recovery.is_migration_recovery_auto_disabled",
        return_value=True,
    )
    def test_disabled_when_db_flag_set(
        self,
        _mock_auto_disabled: MagicMock,
        _mock_env: MagicMock,
        _mock_listener: MagicMock,
    ) -> None:
        from club_gc_settings import is_migration_recovery_enabled

        self.assertFalse(is_migration_recovery_enabled())

    @patch("club_gc_settings.is_dm_gc_listener_enabled", return_value=True)
    @patch("club_gc_settings._env_bool", return_value=True)
    @patch(
        "bot.services.migration_recovery.is_migration_recovery_auto_disabled",
        return_value=False,
    )
    def test_enabled_when_env_on_and_no_db_flag(
        self,
        _mock_auto_disabled: MagicMock,
        _mock_env: MagicMock,
        _mock_listener: MagicMock,
    ) -> None:
        from club_gc_settings import is_migration_recovery_enabled

        self.assertTrue(is_migration_recovery_enabled())


class TestPeekNextRecoveryRows(unittest.TestCase):
    def _make_db_row(self, **kwargs):
        row = MagicMock()
        defaults = {
            "id": 1,
            "telegram_chat_id": -1001,
            "club_key": "round_table",
            "club_id": 2,
            "group_title": "RT test",
            "old_chat_id": -501,
            "player_telegram_user_id": None,
            "player_username": None,
        }
        defaults.update(kwargs)
        for key, value in defaults.items():
            setattr(row, key, value)
        return row

    @patch("db.connection.get_db")
    def test_peek_returns_pending_in_order(self, mock_get_db: MagicMock) -> None:
        high = self._make_db_row(id=1, priority_tier=1, priority_rank=1)
        low = self._make_db_row(id=2, priority_tier=2, priority_rank=1)
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        query = MagicMock()
        session.query.return_value = query
        query.filter.return_value = query
        query.order_by.return_value = query
        query.limit.return_value = query
        query.all.return_value = [high, low]

        rows = peek_next_recovery_rows(limit=10)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].id, 1)
        query.limit.assert_called_with(10)


class TestReleaseProcessingRows(unittest.TestCase):
    @patch("db.connection.get_db")
    def test_releases_processing_rows(self, mock_get_db: MagicMock) -> None:
        row = MagicMock()
        row.readd_status = "processing"
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        session.get.return_value = row

        released = release_processing_rows([42])
        self.assertEqual(released, 1)
        self.assertEqual(row.readd_status, "pending")
        self.assertIsNone(row.readd_attempted_at)


class TestHandleRateLimitAbort(unittest.TestCase):
    def test_rate_limit_triggers_auto_disable(self) -> None:
        row = RecoveryRow(
            id=5,
            telegram_chat_id=-100999,
            club_key="clubgto",
            club_id=4,
            group_title="GTO test",
            old_chat_id=-1,
            player_telegram_user_id=None,
            player_username=None,
        )
        with patch(
            "bot.services.migration_recovery.finalize_row",
            return_value="failed",
        ) as mock_finalize, patch(
            "bot.services.migration_recovery.release_processing_rows",
            return_value=1,
        ) as mock_release, patch(
            "bot.services.migration_recovery.pending_count_by_club",
            return_value={"round_table": 1, "creator_club": 2, "clubgto": 3},
        ), patch(
            "bot.services.migration_recovery.auto_disable_migration_recovery",
            new_callable=AsyncMock,
        ) as mock_disable, patch(
            "bot.services.mtproto_track_contact.notify_all_gc_admins_dm",
            new_callable=AsyncMock,
        ) as mock_notify:
            asyncio.run(
                _handle_rate_limit_abort(
                    exc=FloodWaitAbortError(90, "InviteToChannelRequest"),
                    row=row,
                    remaining_row_ids=[6, 7],
                )
            )
        mock_finalize.assert_called_once()
        mock_release.assert_called_once_with([6, 7])
        mock_notify.assert_awaited_once()
        mock_disable.assert_awaited_once_with(
            reason="rate_limit",
            exhausted_club_key="clubgto",
            pending_snapshot={"round_table": 1, "creator_club": 2, "clubgto": 3},
        )

    def test_rate_limit_notification_includes_gc(self) -> None:
        row = RecoveryRow(
            id=1,
            telegram_chat_id=-1001,
            club_key="round_table",
            club_id=2,
            group_title="RT / test",
            old_chat_id=-1,
            player_telegram_user_id=None,
            player_username=None,
        )
        text = format_rate_limit_admin_notification(
            wait_s=60,
            label="get_participants",
            row=row,
        )
        self.assertIn("FloodWait", text)
        self.assertIn("RT / test", text)
        self.assertIn("round_table", text)


if __name__ == "__main__":
    unittest.main()
