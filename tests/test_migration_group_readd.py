"""Tests for migration re-add (player-only path)."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.migration_group_readd import (
    ReaddGroupResult,
    invite_user_id,
    readd_group,
)
from bot.services.migration_recovery import RecoveryRow, _process_row
from scripts.backfill_support_group_invite_links import LinkedGroupRow


async def _passthrough_flood_retry(factory, *, label: str):
    return await factory()


class TestInviteUserId(unittest.IsolatedAsyncioTestCase):
    @patch(
        "bot.services.migration_group_readd.call_with_flood_retry",
        side_effect=_passthrough_flood_retry,
    )
    async def test_already_member_skips_invite(self, _mock_flood: MagicMock) -> None:
        from telethon.tl.functions.channels import GetParticipantRequest, InviteToChannelRequest

        mock_user = MagicMock()
        mock_user.id = 123
        channel_entity = MagicMock()
        invite_calls: list[object] = []

        client = AsyncMock()

        async def _client_call(request):
            if isinstance(request, GetParticipantRequest):
                return MagicMock()
            if isinstance(request, InviteToChannelRequest):
                invite_calls.append(request)
                return MagicMock()
            raise AssertionError(f"unexpected request: {type(request)}")

        client.side_effect = _client_call

        status, reason = await invite_user_id(
            client,
            channel_entity,
            123,
            apply=True,
        )

        self.assertEqual(status, "already_member")
        self.assertIsNone(reason)
        self.assertEqual(invite_calls, [])

    @patch(
        "bot.services.migration_group_readd.call_with_flood_retry",
        side_effect=_passthrough_flood_retry,
    )
    async def test_not_member_invites_once(self, _mock_flood: MagicMock) -> None:
        from telethon.errors.rpcerrorlist import UserNotParticipantError
        from telethon.tl.functions.channels import GetParticipantRequest, InviteToChannelRequest

        mock_user = MagicMock()
        mock_user.id = 456
        channel_entity = MagicMock()
        invite_calls: list[object] = []

        client = AsyncMock()

        async def _client_call(request):
            if isinstance(request, GetParticipantRequest):
                raise UserNotParticipantError(request=None)
            if isinstance(request, InviteToChannelRequest):
                invite_calls.append(request)
                return MagicMock()
            raise AssertionError(f"unexpected request: {type(request)}")

        client.side_effect = _client_call

        status, reason = await invite_user_id(
            client,
            channel_entity,
            456,
            apply=True,
        )

        self.assertEqual(status, "added")
        self.assertIsNone(reason)
        self.assertEqual(len(invite_calls), 1)

    @patch(
        "bot.services.migration_group_readd.call_with_flood_retry",
        side_effect=_passthrough_flood_retry,
    )
    async def test_flood_wait_abort_propagates(self, _mock_flood: MagicMock) -> None:
        from telethon.errors.rpcerrorlist import UserNotParticipantError
        from telethon.tl.functions.channels import GetParticipantRequest, InviteToChannelRequest

        from bot.services.migration_group_readd import FloodWaitAbortError

        mock_user = MagicMock()
        mock_user.id = 789
        channel_entity = MagicMock()
        client = AsyncMock()

        async def _client_call(request):
            if isinstance(request, GetParticipantRequest):
                raise UserNotParticipantError(request=None)
            if isinstance(request, InviteToChannelRequest):
                return MagicMock()
            raise AssertionError(f"unexpected request: {type(request)}")

        client.side_effect = _client_call

        async def _abort_on_invite(factory, *, label: str):
            if label.startswith("InviteToChannel"):
                raise FloodWaitAbortError(90, label)
            return await factory()

        _mock_flood.side_effect = _abort_on_invite

        with self.assertRaises(FloodWaitAbortError) as ctx:
            await invite_user_id(client, channel_entity, 789, apply=True)

        self.assertEqual(ctx.exception.wait_s, 90)
        self.assertIn("InviteToChannel", ctx.exception.label)


class TestReaddGroupPlayerOnly(unittest.IsolatedAsyncioTestCase):
    def _group(self) -> LinkedGroupRow:
        return LinkedGroupRow(chat_id=-1001, club_id=2, title="RT / / @player1")

    def _cfg(self) -> MagicMock:
        cfg = MagicMock()
        cfg.club_key = "round_table"
        cfg.club_display_name = "Round Table"
        cfg.mtproto_session = "sessions/round_table.session"
        return cfg

    @patch(
        "bot.services.migration_group_readd.call_with_flood_retry",
        new_callable=AsyncMock,
        return_value=MagicMock(),
    )
    @patch(
        "bot.services.migration_group_readd.participant_user_ids",
        new_callable=AsyncMock,
    )
    @patch(
        "bot.services.migration_group_readd.invite_user_id",
        new_callable=AsyncMock,
        return_value=("already_member", None),
    )
    async def test_player_only_skips_participant_list_when_already_in(
        self,
        _mock_invite: AsyncMock,
        mock_participants: AsyncMock,
        _mock_entity: AsyncMock,
    ) -> None:
        result = await readd_group(
            client=MagicMock(),
            cfg=self._cfg(),
            group=self._group(),
            dialog_chat_id=-1001,
            player_id=111,
            player_username="player1",
            apply=True,
            update_invite_links=True,
            invite_staff=False,
            listener_user_id=999,
        )

        mock_participants.assert_not_awaited()
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.already_member, ["player:@player1"])
        self.assertEqual(result.added, [])

    @patch(
        "bot.services.migration_group_readd.call_with_flood_retry",
        new_callable=AsyncMock,
        return_value=MagicMock(),
    )
    @patch(
        "bot.services.migration_group_readd.participant_user_ids",
        new_callable=AsyncMock,
    )
    @patch(
        "bot.services.migration_group_readd.invite_user_id",
        new_callable=AsyncMock,
        return_value=("added", None),
    )
    async def test_player_only_adds_without_participant_list(
        self,
        mock_invite: AsyncMock,
        mock_participants: AsyncMock,
        _mock_entity: AsyncMock,
    ) -> None:
        result = await readd_group(
            client=MagicMock(),
            cfg=self._cfg(),
            group=self._group(),
            dialog_chat_id=-1001,
            player_id=111,
            player_username="player1",
            apply=True,
            update_invite_links=True,
            invite_staff=False,
            listener_user_id=999,
        )

        mock_participants.assert_not_awaited()
        mock_invite.assert_awaited_once()
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.added, ["player:@player1"])

    @patch(
        "bot.services.migration_group_readd.call_with_flood_retry",
        new_callable=AsyncMock,
        return_value=MagicMock(),
    )
    async def test_player_only_no_player_id_is_no_targets(
        self,
        _mock_entity: AsyncMock,
    ) -> None:
        result = await readd_group(
            client=MagicMock(),
            cfg=self._cfg(),
            group=self._group(),
            dialog_chat_id=-1001,
            player_id=None,
            player_username=None,
            apply=True,
            update_invite_links=True,
            invite_staff=False,
            listener_user_id=None,
        )

        self.assertEqual(result.status, "no_targets")


class TestMigrationRecoveryProcessRow(unittest.IsolatedAsyncioTestCase):
    @patch("bot.services.migration_recovery._notify_rt_ops_if_needed", new_callable=AsyncMock)
    @patch("bot.services.migration_recovery.notify_readd_admin_dm", new_callable=AsyncMock)
    @patch("bot.services.migration_recovery.finalize_row", return_value="complete")
    @patch("bot.services.migration_recovery.readd_group", new_callable=AsyncMock)
    @patch("bot.services.mtproto_dm_gc_listener.get_listener_client")
    @patch("bot.services.migration_recovery.get_club_gc_config_by_link_club_id")
    async def test_process_row_passes_invite_staff_false(
        self,
        mock_get_cfg: MagicMock,
        mock_get_client: MagicMock,
        mock_readd: AsyncMock,
        _mock_finalize: MagicMock,
        _mock_notify_admin: AsyncMock,
        _mock_notify_ops: AsyncMock,
    ) -> None:
        mock_get_cfg.return_value = MagicMock(
            club_key="round_table",
            club_display_name="Round Table",
        )
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        mock_client.get_me = AsyncMock(return_value=MagicMock(id=999))
        mock_get_client.return_value = mock_client
        mock_readd.return_value = ReaddGroupResult(
            chat_id=-1001,
            club_id=2,
            club_key="round_table",
            title="RT / / @player1",
            member_count_before=0,
            member_count_after=None,
            status="ok",
            already_member=["player:@player1"],
        )

        row = RecoveryRow(
            id=1,
            telegram_chat_id=-1001,
            club_key="round_table",
            club_id=2,
            group_title="RT / / @player1",
            old_chat_id=-456,
            player_telegram_user_id=111,
            player_username="player1",
        )

        await _process_row(row)

        mock_readd.assert_awaited_once()
        self.assertFalse(mock_readd.await_args.kwargs["invite_staff"])


if __name__ == "__main__":
    unittest.main()
