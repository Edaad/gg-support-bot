"""Tests for bonus player resolution from group title."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bot.services.bonus_player_resolve import resolve_bonus_player


class TestResolveBonusPlayer(unittest.TestCase):
    @patch("bot.services.player_details_nickname.try_refresh_nickname_after_bind")
    @patch("bot.services.bonus_player_resolve.build_zapier_name", return_value=None)
    @patch("bot.services.bonus_player_resolve._lookup_player_details_id", return_value=42)
    @patch("bot.services.bonus_player_resolve.bind_chat_to_player")
    @patch("bot.services.bonus_player_resolve.resolve_club_id_from_shorthand", return_value=3)
    def test_resolves_with_chat_bind(
        self,
        _resolve_club,
        mock_bind,
        _lookup,
        _zapier_name,
        _refresh,
    ) -> None:
        title = "CC / 8190-5287 / Jacob"
        ctx = resolve_bonus_player(
            group_title=title,
            chat_id=-100123,
            club_id=3,
        )
        self.assertIsNotNone(ctx)
        assert ctx is not None
        self.assertEqual(ctx.group_title, title)
        self.assertEqual(ctx.gg_player_id, "8190-5287")
        self.assertEqual(ctx.club_id, 3)
        self.assertEqual(ctx.chat_id, -100123)
        self.assertEqual(ctx.player_details_id, 42)
        self.assertEqual(ctx.zapier_name, "CC / 8190-5287 / Jacob")
        mock_bind.assert_called_once_with(
            club_id=3,
            gg_player_id="8190-5287",
            chat_id=-100123,
        )

    @patch("bot.services.bonus_player_resolve.resolve_club_id_from_shorthand", return_value=3)
    def test_club_mismatch_returns_none(self, _resolve_club) -> None:
        ctx = resolve_bonus_player(
            group_title="CC / 8190-5287 / Jacob",
            chat_id=-100123,
            club_id=99,
        )
        self.assertIsNone(ctx)

    def test_invalid_title_returns_none(self) -> None:
        self.assertIsNone(resolve_bonus_player(group_title="not a valid title"))

    @patch("bot.services.bonus_player_resolve.build_zapier_name", return_value="CC / 8190-5287 / Jacob")
    @patch("bot.services.bonus_player_resolve._lookup_player_details_id", return_value=7)
    @patch("bot.services.bonus_player_resolve.resolve_club_id_from_shorthand", return_value=3)
    def test_standalone_without_chat_id(self, _resolve_club, _lookup, _zapier) -> None:
        with patch("bot.services.bonus_player_resolve.bind_chat_to_player") as mock_bind:
            ctx = resolve_bonus_player(group_title="CC / 8190-5287 / Jacob")
        self.assertIsNotNone(ctx)
        mock_bind.assert_not_called()
        assert ctx is not None
        self.assertIsNone(ctx.chat_id)
        self.assertEqual(ctx.zapier_name, "CC / 8190-5287 / Jacob")


if __name__ == "__main__":
    unittest.main()
