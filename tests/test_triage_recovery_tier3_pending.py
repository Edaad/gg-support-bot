"""Tests for tier-3 pending recovery triage helpers."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from unittest.mock import AsyncMock, MagicMock, patch

from scripts.migrated_groups_activity_report import GroupAgg
from scripts.triage_recovery_tier3_pending import (
    RecoveryRowForTriage,
    account_check_from_resolved_user,
    build_triage_csv_row,
    classify_entity_failure_repair,
    classify_triage_action,
    finalize_triage_decision,
    row_has_entity_resolution_failure,
    PlayerAccountResolution,
    _discover_player_from_group_messages,
    _resolve_player_account,
)


def _sample_row(**kwargs) -> RecoveryRowForTriage:
    defaults = dict(
        row_id=42,
        cohort="tier3_pending",
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
        row_last_error=None,
    )
    defaults.update(kwargs)
    return RecoveryRowForTriage(**defaults)


class TestEntityResolutionFailureDetection(unittest.TestCase):
    def test_detects_entity_resolution_failed_in_last_error(self) -> None:
        self.assertTrue(
            row_has_entity_resolution_failure(
                last_error="player:@x:entity_resolution_failed",
                readd_result=None,
            )
        )

    def test_detects_value_error_in_readd_result(self) -> None:
        self.assertTrue(
            row_has_entity_resolution_failure(
                last_error=None,
                readd_result={
                    "failed": [
                        "player:1:Could not find the input entity for PeerUser"
                    ]
                },
            )
        )

    def test_ignores_unrelated_failure(self) -> None:
        self.assertFalse(
            row_has_entity_resolution_failure(
                last_error="privacy_blocked",
                readd_result=None,
            )
        )


class TestClassifyEntityFailureRepair(unittest.TestCase):
    def test_alive_repairs(self) -> None:
        decision = classify_entity_failure_repair(
            PlayerAccountResolution(
                account_check="alive",
                user_id=1779692689,
                username="derek",
            )
        )
        self.assertEqual(decision.action, "repair_pending")
        self.assertEqual(decision.discovered_player_id, 1779692689)

    def test_not_found_drops(self) -> None:
        decision = classify_entity_failure_repair(
            PlayerAccountResolution(account_check="not_found")
        )
        self.assertEqual(decision.action, "drop_deleted")


class TestAccountCheckFromResolvedUser(unittest.TestCase):
    def test_none_is_not_found(self) -> None:
        self.assertEqual(account_check_from_resolved_user(None, expected_user_id=1), "not_found")

    def test_matching_id_is_alive(self) -> None:
        user = MagicMock(id=1779692689, deleted=False)
        self.assertEqual(
            account_check_from_resolved_user(user, expected_user_id=1779692689),
            "alive",
        )

    def test_mismatched_id_is_not_found(self) -> None:
        user = MagicMock(id=999, deleted=False)
        self.assertEqual(
            account_check_from_resolved_user(user, expected_user_id=1779692689),
            "not_found",
        )

    def test_deleted_flag(self) -> None:
        user = MagicMock(id=1779692689, deleted=True)
        self.assertEqual(
            account_check_from_resolved_user(user, expected_user_id=1779692689),
            "deleted",
        )


class TestResolvePlayerAccountOldChatFallback(unittest.IsolatedAsyncioTestCase):
    @patch(
        "bot.services.migration_group_readd.resolve_player_entity_for_readd",
        new_callable=AsyncMock,
    )
    @patch(
        "scripts.triage_recovery_tier3_pending.call_with_flood_retry",
        new_callable=AsyncMock,
    )
    async def test_uses_resolve_with_old_chat_id(
        self,
        mock_flood: AsyncMock,
        mock_resolve: AsyncMock,
    ) -> None:
        current_ent = MagicMock()
        derek = MagicMock(id=1779692689, deleted=False)
        mock_flood.return_value = current_ent
        mock_resolve.return_value = (derek, "old_chat_message_sender")

        result = await _resolve_player_account(
            AsyncMock(),
            MagicMock(),
            player_telegram_user_id=1779692689,
            player_username=None,
            telegram_chat_id=-1003731639251,
            old_chat_id=-5253511706,
            self_id=999,
        )

        self.assertEqual(result.account_check, "alive")
        mock_resolve.assert_awaited_once()
        _args, kwargs = mock_resolve.await_args
        self.assertEqual(kwargs["stored_id"], 1779692689)
        self.assertEqual(kwargs["old_chat_id"], -5253511706)


class TestDiscoverPlayerFromGroupMessages(unittest.IsolatedAsyncioTestCase):
    @patch(
        "bot.services.mtproto_group_player.find_latest_eligible_message_sender",
        new_callable=AsyncMock,
    )
    @patch(
        "scripts.triage_recovery_tier3_pending.call_with_flood_retry",
        new_callable=AsyncMock,
    )
    async def test_falls_back_to_old_chat(
        self,
        mock_flood: AsyncMock,
        mock_find_sender: AsyncMock,
    ) -> None:
        current_ent = MagicMock()
        old_ent = MagicMock()
        player = MagicMock(id=555, deleted=False)

        async def _flood(factory, *, label: str):
            if "old_chat" in label:
                return old_ent
            return current_ent

        async def _find_sender(client, channel_ent, cfg, *, self_id, limit=50):
            if channel_ent is current_ent:
                return None
            if channel_ent is old_ent:
                return player
            return None

        mock_flood.side_effect = _flood
        mock_find_sender.side_effect = _find_sender

        found = await _discover_player_from_group_messages(
            AsyncMock(),
            MagicMock(),
            telegram_chat_id=-1001,
            old_chat_id=-501,
            self_id=999,
        )

        self.assertIs(found, player)
        self.assertEqual(mock_find_sender.await_count, 2)


class TestResolvePlayerAccountWithoutStoredId(unittest.IsolatedAsyncioTestCase):
    @patch(
        "scripts.triage_recovery_tier3_pending._discover_player_from_group_messages",
        new_callable=AsyncMock,
    )
    async def test_discovers_via_messages_when_no_stored_id(
        self,
        mock_discover: AsyncMock,
    ) -> None:
        player = MagicMock(id=777, deleted=False)
        mock_discover.return_value = player

        result = await _resolve_player_account(
            AsyncMock(),
            MagicMock(),
            player_telegram_user_id=None,
            player_username=None,
            telegram_chat_id=-1001,
            old_chat_id=-501,
            self_id=999,
        )

        self.assertEqual(result.account_check, "alive")
        mock_discover.assert_awaited_once()


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
