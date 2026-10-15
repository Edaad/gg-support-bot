"""Tests for migration re-add (player-only path)."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.migration_group_readd import (
    invite_user_id,
    is_entity_resolution_error,
    readd_group,
    resolve_player_entity_for_readd,
)
from scripts.backfill_support_group_invite_links import LinkedGroupRow


async def _passthrough_flood_retry(factory, *, label: str):
    return await factory()


def _mock_resolved_player(player_id: int = 111, username: str = "player1"):
    user = MagicMock()
    user.id = player_id
    user.username = username
    user.first_name = "Test"
    user.last_name = "Player"
    user.bot = False
    return user


class TestEntityResolutionError(unittest.TestCase):
    def test_recognizes_telethon_value_error(self) -> None:
        exc = ValueError(
            "Could not find the input entity for PeerUser(user_id=8226300069) "
            "(PeerUser). Please read https://docs.telethon.dev/..."
        )
        self.assertTrue(is_entity_resolution_error(exc))

    def test_recognizes_dead_username(self) -> None:
        exc = ValueError('No user has "howard123457" as username')
        self.assertTrue(is_entity_resolution_error(exc))

    def test_recognizes_username_not_occupied_type_name(self) -> None:
        exc = type("UsernameNotOccupiedError", (Exception,), {})(
            "The username is not in use by anyone else yet"
        )
        self.assertTrue(is_entity_resolution_error(exc))

    def test_ignores_other_errors(self) -> None:
        self.assertFalse(is_entity_resolution_error(RuntimeError("network down")))


class TestInviteUserId(unittest.IsolatedAsyncioTestCase):
    @patch(
        "bot.services.migration_group_readd.call_with_flood_retry",
        side_effect=_passthrough_flood_retry,
    )
    async def test_already_member_skips_invite(self, _mock_flood: MagicMock) -> None:
        from telethon.tl.functions.channels import (
            GetParticipantRequest,
            InviteToChannelRequest,
        )

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
        from telethon.tl.functions.channels import (
            GetParticipantRequest,
            InviteToChannelRequest,
        )

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
    async def test_user_entity_skips_get_entity(self, mock_flood: MagicMock) -> None:
        from telethon.errors.rpcerrorlist import UserNotParticipantError
        from telethon.tl.functions.channels import (
            GetParticipantRequest,
            InviteToChannelRequest,
        )

        mock_user = MagicMock()
        mock_user.id = 321
        channel_entity = MagicMock()
        client = AsyncMock()

        async def _client_call(request):
            if isinstance(request, GetParticipantRequest):
                raise UserNotParticipantError(request=None)
            if isinstance(request, InviteToChannelRequest):
                return MagicMock()
            raise AssertionError(f"unexpected request: {type(request)}")

        client.side_effect = _client_call

        status, reason = await invite_user_id(
            client,
            channel_entity,
            321,
            apply=True,
            user_entity=mock_user,
        )

        self.assertEqual(status, "added")
        self.assertIsNone(reason)
        get_entity_calls = [
            call
            for call in mock_flood.call_args_list
            if call.kwargs.get("label", "").startswith("get_entity:")
        ]
        self.assertEqual(get_entity_calls, [])

    @patch(
        "bot.services.migration_group_readd.call_with_flood_retry",
        side_effect=_passthrough_flood_retry,
    )
    async def test_flood_wait_abort_propagates(self, _mock_flood: MagicMock) -> None:
        from telethon.errors.rpcerrorlist import UserNotParticipantError
        from telethon.tl.functions.channels import (
            GetParticipantRequest,
            InviteToChannelRequest,
        )

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
        "bot.services.migration_group_readd.resolve_player_entity_for_readd",
        new_callable=AsyncMock,
    )
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
        mock_resolve: AsyncMock,
    ) -> None:
        mock_resolve.return_value = (_mock_resolved_player(), "stored_id")
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
        "bot.services.migration_group_readd.resolve_player_entity_for_readd",
        new_callable=AsyncMock,
    )
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
        mock_resolve: AsyncMock,
    ) -> None:
        mock_resolve.return_value = (_mock_resolved_player(), "stored_id")
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

    @patch(
        "bot.services.migration_group_readd.resolve_player_entity_for_readd",
        new_callable=AsyncMock,
        return_value=(None, "unresolved"),
    )
    @patch(
        "bot.services.migration_group_readd.call_with_flood_retry",
        new_callable=AsyncMock,
        return_value=MagicMock(),
    )
    async def test_player_only_entity_resolution_failed(
        self,
        _mock_entity: AsyncMock,
        _mock_resolve: AsyncMock,
    ) -> None:
        result = await readd_group(
            client=MagicMock(),
            cfg=self._cfg(),
            group=self._group(),
            dialog_chat_id=-1001,
            player_id=8226300069,
            player_username=None,
            apply=True,
            update_invite_links=True,
            invite_staff=False,
            listener_user_id=999,
        )

        self.assertEqual(result.status, "partial")
        self.assertTrue(any("entity_resolution_failed" in x for x in result.failed))

    @patch(
        "bot.services.migration_group_readd.invite_user_id",
        new_callable=AsyncMock,
        return_value=("already_member", None),
    )
    @patch(
        "bot.services.migration_group_readd.resolve_player_entity_for_readd",
        new_callable=AsyncMock,
    )
    @patch(
        "bot.services.migration_group_readd.call_with_flood_retry",
        new_callable=AsyncMock,
        return_value=MagicMock(),
    )
    async def test_player_only_message_sender_fallback_metadata(
        self,
        _mock_entity: AsyncMock,
        mock_resolve: AsyncMock,
        mock_invite: AsyncMock,
    ) -> None:
        resolved = _mock_resolved_player(player_id=555555, username="realplayer")
        mock_resolve.return_value = (resolved, "message_sender")

        result = await readd_group(
            client=MagicMock(),
            cfg=self._cfg(),
            group=self._group(),
            dialog_chat_id=-1001,
            player_id=8226300069,
            player_username=None,
            apply=True,
            update_invite_links=True,
            invite_staff=False,
            listener_user_id=999,
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.resolved_player_id, 555555)
        self.assertEqual(result.resolved_player_source, "message_sender")
        mock_invite.assert_awaited_once()
        self.assertIs(mock_invite.await_args.kwargs["user_entity"], resolved)


class TestResolvePlayerEntityForReadd(unittest.IsolatedAsyncioTestCase):
    @patch(
        "bot.services.mtproto_group_player.find_latest_eligible_message_sender",
        new_callable=AsyncMock,
    )
    @patch(
        "bot.services.migration_group_readd.call_with_flood_retry",
        side_effect=_passthrough_flood_retry,
    )
    async def test_falls_back_to_message_sender(
        self,
        _mock_flood: MagicMock,
        mock_find_sender: AsyncMock,
    ) -> None:
        entity_err = ValueError(
            "Could not find the input entity for PeerUser(user_id=1)"
        )
        sender = _mock_resolved_player(player_id=99, username="fromchat")
        mock_find_sender.return_value = sender
        client = AsyncMock()
        client.get_entity = AsyncMock(side_effect=entity_err)

        user, source = await resolve_player_entity_for_readd(
            client,
            MagicMock(),
            MagicMock(),
            stored_id=1,
            stored_username=None,
            self_id=999,
        )

        self.assertIs(user, sender)
        self.assertEqual(source, "message_sender")
        mock_find_sender.assert_awaited_once()

    @patch(
        "bot.services.mtproto_group_player.find_latest_eligible_message_sender",
        new_callable=AsyncMock,
    )
    @patch(
        "bot.services.migration_group_readd.call_with_flood_retry",
        side_effect=_passthrough_flood_retry,
    )
    async def test_falls_back_to_old_chat_message_sender(
        self,
        _mock_flood: MagicMock,
        mock_find_sender: AsyncMock,
    ) -> None:
        entity_err = ValueError(
            "Could not find the input entity for PeerUser(user_id=1)"
        )
        sender = _mock_resolved_player(player_id=1779692689, username="derek")
        current_ent = MagicMock()
        old_ent = MagicMock()

        async def _find_sender(client, channel_ent, cfg, *, self_id, limit=50):
            if channel_ent is current_ent:
                return None
            if channel_ent is old_ent:
                return sender
            return None

        mock_find_sender.side_effect = _find_sender

        client = AsyncMock()

        async def _get_entity(cid):
            if int(cid) == 1779692689:
                raise entity_err
            if int(cid) == -5253511706:
                return old_ent
            return current_ent

        client.get_entity = AsyncMock(side_effect=_get_entity)

        user, source = await resolve_player_entity_for_readd(
            client,
            current_ent,
            MagicMock(),
            stored_id=1779692689,
            stored_username=None,
            self_id=999,
            old_chat_id=-5253511706,
        )

        self.assertIs(user, sender)
        self.assertEqual(source, "old_chat_message_sender")
        self.assertEqual(mock_find_sender.await_count, 2)


if __name__ == "__main__":
    unittest.main()
