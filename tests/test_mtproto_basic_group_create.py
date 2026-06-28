"""Tests for basic-group MTProto creation helpers."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telethon.tl.types import Channel, Chat, User

from bot.services.mtproto_group_create import (
    _add_user_to_group,
    _export_invite_link,
    _is_channel_entity,
    _marker_matches_user,
    _resolve_seed_user_for_create_chat,
    ensure_player_in_support_group,
    export_invite_link_for_peer,
)


class TestEntityHelpers(unittest.TestCase):
    def test_is_channel_entity(self) -> None:
        self.assertTrue(_is_channel_entity(MagicMock(spec=Channel)))
        self.assertFalse(_is_channel_entity(MagicMock(spec=Chat)))

    def test_marker_matches_user(self) -> None:
        user = MagicMock(spec=User)
        user.id = 123
        user.username = "player1"
        self.assertTrue(_marker_matches_user("@player1", user))
        self.assertTrue(_marker_matches_user("123", user))
        self.assertFalse(_marker_matches_user("@other", user))


class TestResolveSeedUser(unittest.IsolatedAsyncioTestCase):
    async def test_player_first(self) -> None:
        player = MagicMock(spec=User)
        client = MagicMock()
        seed = await _resolve_seed_user_for_create_chat(
            client, player_user=player, invite_targets=["@staff"]
        )
        self.assertIs(seed, player)

    async def test_falls_back_to_invite_target(self) -> None:
        staff = MagicMock(spec=User)
        staff.id = 99
        client = MagicMock()
        client.get_entity = AsyncMock(return_value=staff)

        async def flood_retry(_tag, factory):
            result = factory()
            if hasattr(result, "__await__"):
                return await result
            return result

        with patch(
            "bot.services.mtproto_group_create._with_single_flood_retry",
            new_callable=AsyncMock,
            side_effect=flood_retry,
        ):
            seed = await _resolve_seed_user_for_create_chat(
                client, player_user=None, invite_targets=["@staff"]
            )
        self.assertIs(seed, staff)

    async def test_none_when_unresolvable(self) -> None:
        client = MagicMock()
        client.get_entity = AsyncMock(side_effect=RuntimeError("nope"))

        async def flood_retry(_tag, factory):
            result = factory()
            if hasattr(result, "__await__"):
                return await result
            return result

        with patch(
            "bot.services.mtproto_group_create._with_single_flood_retry",
            new_callable=AsyncMock,
            side_effect=flood_retry,
        ):
            seed = await _resolve_seed_user_for_create_chat(
                client, player_user=None, invite_targets=["@missing"]
            )
        self.assertIsNone(seed)


class TestAddUserToGroup(unittest.IsolatedAsyncioTestCase):
    async def test_channel_uses_invite_to_channel(self) -> None:
        client = MagicMock()
        channel = MagicMock(spec=Channel)
        user = MagicMock(spec=User)
        with patch(
            "bot.services.mtproto_group_create._with_single_flood_retry",
            new_callable=AsyncMock,
            side_effect=lambda _tag, factory: factory(),
        ):
            ok, err = await _add_user_to_group(client, channel, user)
        self.assertTrue(ok)
        self.assertIsNone(err)
        client.assert_called()

    async def test_chat_uses_add_chat_user(self) -> None:
        client = MagicMock()
        chat = MagicMock(spec=Chat)
        chat.id = 42
        user = MagicMock(spec=User)
        with patch(
            "bot.services.mtproto_group_create._with_single_flood_retry",
            new_callable=AsyncMock,
            side_effect=lambda _tag, factory: factory(),
        ):
            ok, err = await _add_user_to_group(client, chat, user)
        self.assertTrue(ok)
        self.assertIsNone(err)
        client.assert_called()


class TestEnsurePlayerInSupportGroup(unittest.IsolatedAsyncioTestCase):
    async def test_already_member_channel(self) -> None:
        client = MagicMock()
        channel = MagicMock(spec=Channel)
        player = MagicMock(spec=User)
        with patch(
            "bot.services.mtproto_group_create._is_user_in_group",
            new_callable=AsyncMock,
            return_value=True,
        ):
            st = await ensure_player_in_support_group(
                client, channel, player, MagicMock()
            )
        self.assertEqual(st, "already_member")

    async def test_invites_when_missing_chat(self) -> None:
        client = MagicMock()
        chat = MagicMock(spec=Chat)
        player = MagicMock(spec=User)
        cfg = MagicMock(group_photo_path=None)
        with (
            patch(
                "bot.services.mtproto_group_create._is_user_in_group",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "bot.services.mtproto_group_create._add_user_to_group",
                new_callable=AsyncMock,
                return_value=(True, None),
            ),
            patch(
                "bot.services.mtproto_group_create.apply_club_group_photo",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            st = await ensure_player_in_support_group(client, chat, player, cfg)
        self.assertEqual(st, "invited_ok")


class TestExportInviteLink(unittest.IsolatedAsyncioTestCase):
    async def test_revoke_previous_uses_legacy_flag(self) -> None:
        client = MagicMock()
        client.get_input_entity = AsyncMock(return_value="inp")
        inv = MagicMock(link="https://t.me/+fresh")
        client.export_chat_invite_link = AsyncMock()

        with patch(
            "bot.services.mtproto_group_create._with_single_flood_retry",
            new_callable=AsyncMock,
            return_value=inv,
        ) as mock_retry:
            link = await _export_invite_link(
                client, MagicMock(), revoke_previous=True
            )

        self.assertEqual(link, "https://t.me/+fresh")
        client.export_chat_invite_link.assert_not_awaited()
        req = mock_retry.await_args_list[0].args[1]()
        self.assertTrue(req.legacy_revoke_permanent)

    async def test_export_invite_link_for_peer_without_revoke(self) -> None:
        client = MagicMock()
        with patch(
            "bot.services.mtproto_group_create._export_invite_link",
            new_callable=AsyncMock,
            return_value="https://t.me/+abc",
        ) as mock_export:
            link = await export_invite_link_for_peer(client, MagicMock())
        self.assertEqual(link, "https://t.me/+abc")
        mock_export.assert_awaited_once_with(
            client, mock_export.await_args.args[1], revoke_previous=False
        )


if __name__ == "__main__":
    unittest.main()
