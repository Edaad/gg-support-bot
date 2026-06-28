"""Tests for inactive group outreach scan helpers."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.inactive_group_outreach import OutreachScanRow, scan_outreach_row
from bot.services.mtproto_group_activity import (
    ExternalActivityResult,
    compute_inactive_flags,
    merge_external_activity,
    resolve_legacy_chat_id,
)


class TestInactiveFlags(unittest.TestCase):
    def test_no_activity_is_inactive_on_both_thresholds(self) -> None:
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        i90, i180 = compute_inactive_flags(None, now=now)
        self.assertTrue(i90)
        self.assertTrue(i180)

    def test_recent_activity_not_inactive(self) -> None:
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        last = now - timedelta(days=10)
        i90, i180 = compute_inactive_flags(last, now=now)
        self.assertFalse(i90)
        self.assertFalse(i180)

    def test_between_90_and_180_days(self) -> None:
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        last = now - timedelta(days=120)
        i90, i180 = compute_inactive_flags(last, now=now)
        self.assertTrue(i90)
        self.assertFalse(i180)


class TestMergeExternalActivity(unittest.TestCase):
    def test_supergroup_support_only_legacy_has_player(self) -> None:
        legacy_ts = datetime(2026, 1, 15, tzinfo=timezone.utc)
        merged = merge_external_activity(
            ExternalActivityResult(None, "support_only"),
            ExternalActivityResult(legacy_ts, "external"),
        )
        self.assertEqual(merged.last_external_message_at, legacy_ts)
        self.assertEqual(merged.activity_merged_from, "legacy")
        self.assertEqual(merged.activity_basis_supergroup, "support_only")
        self.assertEqual(merged.activity_basis_legacy, "external")

    def test_takes_newer_when_both_have_activity(self) -> None:
        sg_ts = datetime(2026, 5, 1, tzinfo=timezone.utc)
        leg_ts = datetime(2026, 3, 1, tzinfo=timezone.utc)
        merged = merge_external_activity(
            ExternalActivityResult(sg_ts, "external"),
            ExternalActivityResult(leg_ts, "external"),
        )
        self.assertEqual(merged.last_external_message_at, sg_ts)
        self.assertEqual(merged.activity_merged_from, "both")

    def test_none_when_both_empty(self) -> None:
        merged = merge_external_activity(
            ExternalActivityResult(None, "empty"),
            ExternalActivityResult(None, "empty"),
        )
        self.assertIsNone(merged.last_external_message_at)
        self.assertEqual(merged.activity_merged_from, "none")


class TestResolveLegacyChatId(unittest.TestCase):
    @patch("bot.services.chat_id_remap.find_legacy_group_chat_id")
    @patch("db.connection.get_db")
    def test_prefers_migrated_group_recovery(
        self,
        mock_get_db: MagicMock,
        mock_find_legacy: MagicMock,
    ) -> None:
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        session.query.return_value.filter.return_value.first.return_value = (-555,)

        legacy = resolve_legacy_chat_id(
            telegram_chat_id=-100999,
            group_title="RT / 1234-5678 / Name",
            club_id=2,
        )
        self.assertEqual(legacy, -555)
        mock_find_legacy.assert_not_called()

    @patch("bot.services.chat_id_remap.find_legacy_group_chat_id", return_value=None)
    @patch("db.connection.get_db")
    def test_falls_back_to_basic_group_title_map(
        self,
        mock_get_db: MagicMock,
        mock_find_legacy: MagicMock,
    ) -> None:
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        session.query.return_value.filter.return_value.first.return_value = None

        legacy = resolve_legacy_chat_id(
            telegram_chat_id=-100999,
            group_title="RT / 1234-5678 / Name",
            club_id=2,
            basic_groups_by_title={"rt / 1234-5678 / name": -777},
        )
        self.assertEqual(legacy, -777)


class TestPlayerSourcePriority(unittest.IsolatedAsyncioTestCase):
    async def test_support_group_chats_wins_over_message_scan(self) -> None:
        row = OutreachScanRow(
            id=1,
            club_key="round_table",
            telegram_chat_id=-100123,
            group_title="RT / 123 / player",
            legacy_chat_id=-456,
            gg_player_id="123",
        )
        client = MagicMock()
        cfg = MagicMock()
        player_map = {-100123: (999, "@bound", "round_table")}

        async def _fake_flood(factory, **_kwargs):
            result = factory()
            if hasattr(result, "__await__"):
                return await result
            return result

        with patch(
            "bot.services.inactive_group_outreach._dual_chat_activity",
            new_callable=AsyncMock,
            return_value=(
                ExternalActivityResult(None, "support_only"),
                ExternalActivityResult(
                    datetime(2025, 1, 1, tzinfo=timezone.utc),
                    "external",
                ),
            ),
        ), patch(
            "bot.services.inactive_group_outreach._discover_player_from_messages",
            new_callable=AsyncMock,
        ) as mock_discover, patch(
            "bot.services.migration_group_readd.call_with_flood_retry",
            side_effect=_fake_flood,
        ), patch(
            "scripts.triage_recovery_tier3_pending.account_check_from_resolved_user",
            return_value="alive",
        ):
            mock_discover.return_value = MagicMock(id=111)
            resolved_user = MagicMock(id=999, deleted=False)
            client.get_entity = AsyncMock(return_value=resolved_user)

            fields = await scan_outreach_row(
                client,
                cfg,
                row,
                self_id=1,
                exclude_user_ids=frozenset({1}),
                history_limit=200,
                player_map=player_map,
            )

        mock_discover.assert_not_called()
        self.assertEqual(fields["player_telegram_user_id"], 999)
        self.assertEqual(fields["player_source"], "support_group_chats")
        self.assertTrue(fields["entity_resolvable"])


if __name__ == "__main__":
    unittest.main()
