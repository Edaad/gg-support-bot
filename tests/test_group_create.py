"""Tests for GG Support bot /gc command."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.group_create import parse_gc_player_args
from bot.services.mtproto_group_create import MtProtoGroupOutcome


class TestParseGcPlayerArgs(unittest.TestCase):
    def test_none_for_empty(self) -> None:
        self.assertIsNone(parse_gc_player_args([]))

    def test_username_with_at(self) -> None:
        self.assertEqual(parse_gc_player_args(["@player1"]), "@player1")

    def test_username_without_at(self) -> None:
        self.assertEqual(parse_gc_player_args(["player1"]), "@player1")

    def test_numeric_id(self) -> None:
        self.assertEqual(parse_gc_player_args(["7564317295"]), "7564317295")


class TestGcCommand(unittest.IsolatedAsyncioTestCase):
    def _make_update(self, *, args: list[str] | None = None) -> MagicMock:
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_chat = MagicMock()
        update.effective_chat.type = "private"
        update.effective_user = MagicMock()
        update.effective_user.id = 6713100304
        return update

    def _make_context(self, args: list[str] | None = None) -> MagicMock:
        context = MagicMock()
        context.args = args or []
        context.bot = MagicMock()
        context.bot.get_me = AsyncMock(return_value=MagicMock(username="GGBot"))
        context.user_data = {}
        return context

    @patch("bot.handlers.group_create.get_club_config_for_admin", return_value=None)
    async def test_denied_when_not_admin(self, _mock_cfg: MagicMock) -> None:
        from bot.handlers.group_create import gc_command

        update = self._make_update()
        context = self._make_context()

        await gc_command(update, context)

        update.message.reply_text.assert_awaited_once_with(
            "You are not allowed to create groups via /gc."
        )

    @patch("bot.handlers.group_create.fetch_support_group_chat_by_club_player")
    @patch("bot.handlers.group_create.resolve_telegram_user_marker", new_callable=AsyncMock)
    @patch("bot.handlers.group_create.create_support_group", new_callable=AsyncMock)
    @patch("bot.handlers.group_create.is_client_authorized", new_callable=AsyncMock, return_value=True)
    @patch("bot.handlers.group_create.get_tg_mtproto_credentials")
    @patch("bot.handlers.group_create.get_club_config_for_admin")
    async def test_player_gc_creates_and_persists_binding(
        self,
        mock_get_cfg: MagicMock,
        _mock_creds: MagicMock,
        _mock_auth: AsyncMock,
        mock_create: AsyncMock,
        mock_resolve: AsyncMock,
        mock_fetch_existing: MagicMock,
    ) -> None:
        from bot.handlers.group_create import gc_command

        cfg = MagicMock()
        cfg.club_key = "round_table"
        cfg.club_display_name = "Round Table"
        cfg.link_club_id = 1
        cfg.mtproto_session = "sessions/round_table.session"
        cfg.group_photo_path = None
        mock_get_cfg.return_value = cfg

        player = MagicMock()
        player.id = 7564317295
        player.username = "carson"
        player.first_name = "Carson"
        player.last_name = "Kern"
        mock_resolve.return_value = (player, None)
        mock_fetch_existing.return_value = None

        mock_create.return_value = MtProtoGroupOutcome(
            ok=True,
            telegram_chat_id=-5287778428,
            telegram_chat_title="RT / / @carson",
            invite_link="https://t.me/+abc",
            added_users=[],
            failed_users=[],
            initial_message_sent=True,
            group_photo_attempted=False,
            group_photo_ok=False,
            player_direct_add_ok=True,
        )

        with (
            patch(
                "bot.handlers.group_create.send_player_dm_via_club",
                new_callable=AsyncMock,
                return_value=(True, None),
            ),
            patch(
                "bot.handlers.group_create.persist_support_group_chat_row",
                return_value=(42, None),
            ) as mock_persist,
            patch("bot.handlers.group_create.ensure_group_chat_linked", return_value=True),
            patch(
                "bot.handlers.group_create.send_post_gc_intro_bundle",
                new_callable=AsyncMock,
            ),
        ):
            update = self._make_update()
            context = self._make_context(["@carson"])
            await gc_command(update, context)

        mock_create.assert_awaited_once()
        self.assertIs(mock_create.await_args.kwargs.get("player_user"), player)
        mock_persist.assert_called_once()
        self.assertEqual(mock_persist.call_args.kwargs["player_telegram_user_id"], 7564317295)
        reply = update.message.reply_text.await_args.args[0]
        self.assertIn("RT / / @carson", reply)
        self.assertIn("Player DM: sent.", reply)


if __name__ == "__main__":
    unittest.main()
