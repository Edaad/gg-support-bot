"""Tests for Round Table Elevate link-join migration recovery."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.migration_group_readd import (
    ElevateJoinResult,
    ReaddGroupResult,
    elevate_join_recovery_group,
    readd_group,
)
from bot.services.migration_recovery import (
    RecoveryRow,
    count_elevate_pending_rows,
    elevate_joined_in_payload,
    find_oldest_elevate_pending_row,
    map_readd_status_with_elevate,
    merge_elevate_into_payload,
)


class TestElevatePayloadHelpers(unittest.TestCase):
    def test_elevate_joined_in_payload(self) -> None:
        self.assertFalse(elevate_joined_in_payload(None))
        self.assertFalse(elevate_joined_in_payload({}))
        self.assertTrue(elevate_joined_in_payload({"elevate_joined": True}))

    def test_merge_elevate_into_payload(self) -> None:
        payload = merge_elevate_into_payload(
            {"added": ["player:@x"]},
            ElevateJoinResult(joined=True),
        )
        self.assertTrue(payload["elevate_joined"])
        self.assertIn("elevate_join_at", payload)

    def test_map_readd_status_processing_when_elevate_pending(self) -> None:
        result = ReaddGroupResult(
            chat_id=-1001,
            club_id=2,
            club_key="round_table",
            title="RT test",
            member_count_before=0,
            member_count_after=None,
            status="ok",
            added=["player:@x"],
        )
        status, err = map_readd_status_with_elevate(
            result,
            elevate=None,
            require_elevate=True,
        )
        self.assertEqual(status, "processing")
        self.assertIsNone(err)

    def test_map_readd_status_complete_when_elevate_joined(self) -> None:
        result = ReaddGroupResult(
            chat_id=-1001,
            club_id=2,
            club_key="round_table",
            title="RT test",
            member_count_before=0,
            member_count_after=None,
            status="ok",
            added=["player:@x"],
        )
        status, err = map_readd_status_with_elevate(
            result,
            elevate=ElevateJoinResult(joined=True),
            require_elevate=True,
        )
        self.assertEqual(status, "complete")
        self.assertIsNone(err)

    def test_map_readd_status_failed_when_elevate_errors(self) -> None:
        result = ReaddGroupResult(
            chat_id=-1001,
            club_id=2,
            club_key="round_table",
            title="RT test",
            member_count_before=0,
            member_count_after=None,
            status="ok",
            already_member=["player:@x"],
        )
        status, err = map_readd_status_with_elevate(
            result,
            elevate=ElevateJoinResult(error="link_join_failed"),
            require_elevate=True,
        )
        self.assertEqual(status, "failed")
        self.assertIn("elevate_join:", err or "")


class TestFindElevatePendingRow(unittest.TestCase):
    @patch("db.connection.get_db")
    def test_find_oldest_skips_elevate_joined(self, mock_get_db: MagicMock) -> None:
        joined = MagicMock()
        joined.id = 1
        joined.telegram_chat_id = -1001
        joined.club_key = "round_table"
        joined.club_id = 2
        joined.group_title = "RT joined"
        joined.old_chat_id = -501
        joined.player_telegram_user_id = 99
        joined.player_username = None
        joined.priority_tier = 2
        joined.priority_rank = 1
        joined.readd_result = {"elevate_joined": True}

        pending = MagicMock()
        pending.id = 2
        pending.telegram_chat_id = -1002
        pending.club_key = "round_table"
        pending.club_id = 2
        pending.group_title = "RT pending"
        pending.old_chat_id = -502
        pending.player_telegram_user_id = 100
        pending.player_username = "@p"
        pending.priority_tier = 2
        pending.priority_rank = 2
        pending.readd_result = {"elevate_joined": False}

        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        query = MagicMock()
        session.query.return_value = query
        query.filter.return_value = query
        query.order_by.return_value = query
        query.all.return_value = [joined, pending]

        row = find_oldest_elevate_pending_row()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.id, 2)


class TestReaddExportInviteAlways(unittest.IsolatedAsyncioTestCase):
    @patch("bot.services.migration_group_readd.export_invite_link", new_callable=AsyncMock)
    @patch("bot.services.migration_group_readd.invite_user_id", new_callable=AsyncMock)
    @patch("bot.services.migration_group_readd.resolve_player_entity_for_readd", new_callable=AsyncMock)
    @patch("bot.services.migration_group_readd.call_with_flood_retry", new_callable=AsyncMock)
    async def test_export_invite_link_always_on_success(
        self,
        mock_flood: AsyncMock,
        mock_resolve: AsyncMock,
        mock_invite: AsyncMock,
        mock_export: AsyncMock,
    ) -> None:
        mock_flood.return_value = MagicMock()
        mock_resolve.return_value = (MagicMock(id=555), "stored_id")
        mock_invite.return_value = ("added", None)
        mock_export.return_value = "https://t.me/+abc"

        cfg = MagicMock()
        cfg.club_key = "round_table"
        cfg.club_display_name = "Round Table"
        cfg.mtproto_session = "sessions/round_table.session"

        group = MagicMock()
        group.chat_id = -1001
        group.club_id = 2
        group.title = "RT test"

        client = MagicMock()
        result = await readd_group(
            client=client,
            cfg=cfg,
            group=group,
            dialog_chat_id=-1001,
            player_id=555,
            player_username=None,
            apply=True,
            update_invite_links=False,
            invite_staff=False,
            listener_user_id=1,
            export_invite_link_always=True,
        )
        self.assertEqual(result.invite_link, "https://t.me/+abc")
        mock_export.assert_awaited_once()


class TestElevateJoinRecoveryGroup(unittest.IsolatedAsyncioTestCase):
    @patch("bot.services.migration_group_readd.participant_user_ids", new_callable=AsyncMock)
    @patch("bot.services.mtproto_group_join.join_chat_via_invite_link", new_callable=AsyncMock)
    @patch("bot.services.mtproto_group_create.make_client")
    @patch("club_gc_settings.get_mtproto_session_config")
    @patch("bot.services.migration_group_readd.call_with_flood_retry", new_callable=AsyncMock)
    async def test_elevate_join_success(
        self,
        mock_flood: AsyncMock,
        mock_cfg: MagicMock,
        mock_make_client: MagicMock,
        mock_join: AsyncMock,
        mock_participants: AsyncMock,
    ) -> None:
        mock_flood.return_value = MagicMock()
        mock_cfg.return_value = MagicMock(club_key="elevate_admin")
        elevate_client = AsyncMock()
        elevate_client.is_user_authorized = AsyncMock(return_value=True)
        elevate_client.get_me = AsyncMock(return_value=MagicMock(id=777))
        elevate_client.connect = AsyncMock()
        elevate_client.disconnect = AsyncMock()
        mock_make_client.return_value = elevate_client
        mock_join.return_value = (MagicMock(), None)
        mock_participants.side_effect = [set(), {777}]

        rt_client = MagicMock()
        result = await elevate_join_recovery_group(
            invite_link="https://t.me/+abc",
            dialog_chat_id=-1001,
            rt_client=rt_client,
            apply=True,
        )
        self.assertTrue(result.joined)


if __name__ == "__main__":
    unittest.main()
