"""Tests for club group photo when a player is added to an existing support group."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.mtproto_group_create import (
    ensure_player_in_support_group,
    entity_has_group_photo,
)


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
                return_value=True,
            ) as mock_photo,
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
                "bot.services.mtproto_group_create._is_user_in_group",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "bot.services.mtproto_group_create.apply_club_group_photo",
                new_callable=AsyncMock,
            ) as mock_photo,
        ):
            result = await ensure_player_in_support_group(client, channel, player, cfg)

        self.assertEqual(result, "already_member")
        mock_photo.assert_not_awaited()


class TestEntityHasGroupPhoto(unittest.TestCase):
    def test_none_and_empty(self) -> None:
        from telethon.tl.types import ChatPhotoEmpty, PhotoEmpty

        self.assertFalse(entity_has_group_photo(MagicMock(photo=None)))
        self.assertFalse(entity_has_group_photo(MagicMock(spec=[])))
        empty = MagicMock()
        empty.photo = ChatPhotoEmpty()
        self.assertFalse(entity_has_group_photo(empty))
        photo_empty = MagicMock()
        photo_empty.photo = PhotoEmpty(id=0)
        self.assertFalse(entity_has_group_photo(photo_empty))

    def test_present(self) -> None:
        from telethon.tl.types import ChatPhoto

        entity = MagicMock()
        entity.photo = ChatPhoto(
            photo_id=123,
            dc_id=2,
            has_video=False,
        )
        self.assertTrue(entity_has_group_photo(entity))


if __name__ == "__main__":
    unittest.main()
