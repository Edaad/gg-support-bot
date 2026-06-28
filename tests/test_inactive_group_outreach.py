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
    merge_external_activity_results,
    resolve_legacy_chat_id,
    resolve_legacy_chat_ids,
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


class TestMergeLegacyActivityResults(unittest.TestCase):
    def test_picks_newest_player_message_across_legacy_chats(self) -> None:
        april = datetime(2025, 4, 26, tzinfo=timezone.utc)
        merged = merge_external_activity_results(
            [
                ExternalActivityResult(None, "empty"),
                ExternalActivityResult(april, "external"),
            ]
        )
        self.assertEqual(merged.last_external_message_at, april)
        self.assertEqual(merged.activity_basis, "external")

    def test_supergroup_support_only_plus_second_legacy_player(self) -> None:
        """CC / 6217-2220 / J pattern: first legacy empty, second has April player msg."""
        april = datetime(2025, 4, 26, 15, 24, tzinfo=timezone.utc)
        supergroup = ExternalActivityResult(None, "support_only")
        legacy_merged = merge_external_activity_results(
            [
                ExternalActivityResult(None, "empty"),
                ExternalActivityResult(april, "external"),
            ]
        )
        merged = merge_external_activity(supergroup, legacy_merged)
        self.assertEqual(merged.last_external_message_at, april)
        self.assertEqual(merged.activity_merged_from, "legacy")
        i90, i180 = compute_inactive_flags(
            merged.last_external_message_at,
            now=datetime(2026, 6, 28, tzinfo=timezone.utc),
        )
        self.assertTrue(i90)
        self.assertTrue(i180)


class TestResolveLegacyChatId(unittest.TestCase):
    @patch("bot.services.chat_id_remap.find_all_legacy_group_chat_ids")
    @patch("db.connection.get_db")
    def test_returns_all_migrated_and_groups_legacy_ids(
        self,
        mock_get_db: MagicMock,
        mock_find_all: MagicMock,
    ) -> None:
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        session.query.return_value.filter.return_value.all.return_value = [
            (-4931698679,),
            (-5252124913,),
        ]
        mock_find_all.return_value = [-4931698679, -5252124913]

        ids = resolve_legacy_chat_ids(
            telegram_chat_id=-1003858810553,
            group_title="CC / 6217-2220 / J",
            club_id=3,
        )
        self.assertEqual(ids, [-5252124913, -4931698679])

    @patch("bot.services.chat_id_remap.find_all_legacy_group_chat_ids")
    @patch("db.connection.get_db")
    def test_prefers_migrated_group_recovery(
        self,
        mock_get_db: MagicMock,
        mock_find_all: MagicMock,
    ) -> None:
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        session.query.return_value.filter.return_value.all.return_value = [(-555,)]
        mock_find_all.return_value = []

        legacy = resolve_legacy_chat_id(
            telegram_chat_id=-100999,
            group_title="RT / 1234-5678 / Name",
            club_id=2,
        )
        self.assertEqual(legacy, -555)
        mock_find_all.assert_called_once()

    @patch("bot.services.chat_id_remap.find_all_legacy_group_chat_ids", return_value=[])
    @patch("db.connection.get_db")
    def test_falls_back_to_basic_group_title_map(
        self,
        mock_get_db: MagicMock,
        mock_find_all: MagicMock,
    ) -> None:
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        session.query.return_value.filter.return_value.all.return_value = []

        ids = resolve_legacy_chat_ids(
            telegram_chat_id=-100999,
            group_title="RT / 1234-5678 / Name",
            club_id=2,
            basic_groups_by_title={"rt / 1234-5678 / name": [-777, -888]},
        )
        self.assertEqual(ids, [-888, -777])


class TestPlayerOnlyActivity(unittest.IsolatedAsyncioTestCase):
    async def test_staff_message_does_not_count_as_activity(self) -> None:
        from bot.services.mtproto_group_activity import last_eligible_player_message_at

        staff_user = MagicMock(id=42, bot=False, username="staff")
        staff_msg = MagicMock(
            date=datetime(2026, 6, 20, tzinfo=timezone.utc),
            sender_id=42,
            out=False,
        )
        staff_msg.get_sender = AsyncMock(return_value=staff_user)

        client = MagicMock()
        client.get_messages = AsyncMock(return_value=[staff_msg])
        client.iter_messages = MagicMock(return_value=self._async_iter([]))
        cfg = MagicMock()

        with patch(
            "bot.services.mtproto_group_player._eligible_player_filter_context",
            new_callable=AsyncMock,
            return_value=(frozenset({42}), frozenset(), frozenset(), frozenset()),
        ), patch(
            "bot.services.mtproto_group_player.is_eligible_player_user",
            return_value=False,
        ):
            result = await last_eligible_player_message_at(
                client, MagicMock(), cfg, self_id=1, history_limit=50
            )

        self.assertIsNone(result.last_external_message_at)
        self.assertEqual(result.activity_basis, "support_only")

    async def _async_iter(self, items):
        for item in items:
            yield item


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
            "bot.services.inactive_group_outreach.resolve_legacy_chat_ids",
            return_value=[-456],
        ), patch(
            "bot.services.inactive_group_outreach._multi_chat_activity",
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
                history_limit=200,
                player_map=player_map,
            )

        mock_discover.assert_not_called()
        self.assertEqual(fields["player_telegram_user_id"], 999)
        self.assertEqual(fields["player_source"], "support_group_chats")
        self.assertTrue(fields["entity_resolvable"])

    async def test_message_scan_when_stored_id_not_resolvable(self) -> None:
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

        scan_user = MagicMock(id=111, deleted=False)

        with patch(
            "bot.services.inactive_group_outreach.resolve_legacy_chat_ids",
            return_value=[-456],
        ), patch(
            "bot.services.inactive_group_outreach._multi_chat_activity",
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
            return_value=scan_user,
        ) as mock_discover, patch(
            "bot.services.migration_group_readd.call_with_flood_retry",
            side_effect=_fake_flood,
        ), patch(
            "scripts.triage_recovery_tier3_pending.account_check_from_resolved_user",
            side_effect=lambda user, **_: "not_found"
            if getattr(user, "id", None) == 999
            else "alive",
        ):
            dead_user = MagicMock(id=999, deleted=False)
            alive_user = MagicMock(id=111, deleted=False)
            client.get_entity = AsyncMock(side_effect=[dead_user, alive_user])

            fields = await scan_outreach_row(
                client,
                cfg,
                row,
                self_id=1,
                history_limit=200,
                player_map=player_map,
            )

        mock_discover.assert_called_once()
        self.assertEqual(fields["player_telegram_user_id"], 111)
        self.assertEqual(fields["player_source"], "message_scan")
        self.assertTrue(fields["entity_resolvable"])


if __name__ == "__main__":
    unittest.main()
