"""Tests for club group photo when a player is added to an existing support group."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.mtproto_group_create import ensure_player_in_support_group


class TestEnsurePlayerInSupportGroupPhoto(unittest.IsolatedAsyncioTestCase):
    async def test_applies_photo_when_player_invited(self) -> None:
        client = MagicMock()
        channel = MagicMock()
        player = MagicMock()
        cfg = MagicMock()
        cfg.club_key = "round_table"
        cfg.group_photo_path = "assets/group_photos/round_table.jpg"

        with (
            patch(
                "bot.services.mtproto_group_create._with_single_flood_retry",
                new_callable=AsyncMock,
                side_effect=Exception("UserNotParticipantError"),
            ),
            patch(
                "bot.services.mtproto_group_create._invite_user_entity",
                new_callable=AsyncMock,
                return_value=(True, None),
            ),
            patch(
                "bot.services.mtproto_group_create.apply_club_group_photo",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_photo,
        ):
            # First _with_single_flood_retry call is GetParticipantRequest — simulate not a member.
            from telethon.errors.rpcerrorlist import UserNotParticipantError

            async def flood_side_effect(_tag, factory):
                if _tag == "GetParticipantRequest":
                    raise UserNotParticipantError(None)
                return await factory()

            with patch(
                "bot.services.mtproto_group_create._with_single_flood_retry",
                side_effect=flood_side_effect,
            ):
                result = await ensure_player_in_support_group(client, channel, player, cfg)

        self.assertEqual(result, "invited_ok")
        mock_photo.assert_awaited_once_with(client, channel, cfg)

    async def test_skips_photo_when_already_member(self) -> None:
        client = MagicMock()
        channel = MagicMock()
        player = MagicMock()
        cfg = MagicMock()
        cfg.club_key = "round_table"

        with (
            patch(
                "bot.services.mtproto_group_create._with_single_flood_retry",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "bot.services.mtproto_group_create.apply_club_group_photo",
                new_callable=AsyncMock,
            ) as mock_photo,
        ):
            result = await ensure_player_in_support_group(client, channel, player, cfg)

        self.assertEqual(result, "already_member")
        mock_photo.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
