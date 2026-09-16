"""Tests for MTProto /delete confirm helpers."""

from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telethon.tl.types import Channel, Chat, User

from bot.handlers.commands import delete_handler
from bot.services.mtproto_group_delete import (
    _kick_all_basic_chat_participants,
    _resolve_club_id_for_delete,
    handle_group_delete_outgoing,
    parse_delete_confirm_command,
)
from bot.services.support_group_chats import delete_active_support_group_records


class TestParseDeleteConfirmCommand(unittest.TestCase):
    def test_exact_match(self) -> None:
        self.assertTrue(parse_delete_confirm_command("/delete confirm"))
        self.assertTrue(parse_delete_confirm_command("/DELETE CONFIRM"))
        self.assertTrue(parse_delete_confirm_command("  /delete confirm  "))

    def test_bot_suffix(self) -> None:
        self.assertTrue(parse_delete_confirm_command("/delete@SomeBot confirm"))

    def test_rejects_bare_or_extra(self) -> None:
        self.assertFalse(parse_delete_confirm_command("/delete"))
        self.assertFalse(parse_delete_confirm_command("/delete confirm extra"))
        self.assertFalse(parse_delete_confirm_command("/add confirm"))


class TestResolveClubIdForDelete(unittest.TestCase):
    @patch("bot.services.mtproto_group_delete.fetch_support_group_chat_row_for_chat")
    @patch("bot.services.mtproto_group_delete.get_club_for_chat")
    def test_groups_table_match(
        self, mock_get_club, mock_fetch_row
    ) -> None:
        cfg = MagicMock(club_key="round_table", link_club_id=2)
        mock_get_club.return_value = 2

        self.assertEqual(_resolve_club_id_for_delete(-100123, cfg), 2)
        mock_fetch_row.assert_not_called()

    @patch("bot.services.mtproto_group_delete.fetch_support_group_chat_row_for_chat")
    @patch("bot.services.mtproto_group_delete.get_club_for_chat")
    def test_support_group_fallback(
        self, mock_get_club, mock_fetch_row
    ) -> None:
        cfg = MagicMock(club_key="round_table", link_club_id=2)
        mock_get_club.return_value = None
        row = MagicMock(club_key="round_table")
        mock_fetch_row.return_value = row

        self.assertEqual(_resolve_club_id_for_delete(-5287778428, cfg), 2)

    @patch("bot.services.mtproto_group_delete.fetch_support_group_chat_row_for_chat")
    @patch("bot.services.mtproto_group_delete.get_club_for_chat")
    def test_wrong_club_rejected(
        self, mock_get_club, mock_fetch_row
    ) -> None:
        cfg = MagicMock(club_key="round_table", link_club_id=2)
        mock_get_club.return_value = 99
        mock_fetch_row.return_value = None

        self.assertIsNone(_resolve_club_id_for_delete(-100123, cfg))


class TestKickBasicChatParticipants(unittest.IsolatedAsyncioTestCase):
    async def test_removes_other_users(self) -> None:
        chat = MagicMock(spec=Chat)
        chat.id = 4242

        keep = MagicMock(spec=User)
        keep.id = 1
        drop = MagicMock(spec=User)
        drop.id = 2

        async def iter_participants(_entity):
            for user in (keep, drop):
                yield user

        client = AsyncMock()
        client.iter_participants = iter_participants

        async def flood_retry_side_effect(_label, fn):
            return await fn()

        with patch(
            "bot.services.mtproto_group_delete._with_single_flood_retry",
            new=AsyncMock(side_effect=flood_retry_side_effect),
        ):
            kicked, failed = await _kick_all_basic_chat_participants(
                client, chat, self_id=1
            )

        self.assertEqual(kicked, 1)
        self.assertEqual(failed, 0)
        client.assert_called()


class TestDeleteActiveSupportGroupRecords(unittest.TestCase):
    @patch(
        "notification.chat_id.telegram_chat_id_variants",
        return_value={-100123, -123},
    )
    @patch("bot.services.support_group_chats.get_db")
    def test_removes_all_active_ownership_records(
        self, mock_get_db, _mock_variants
    ) -> None:
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        session.query.return_value.filter.return_value.delete.side_effect = [1, 2]

        result = delete_active_support_group_records(
            club_id=2,
            club_key="round_table",
            telegram_chat_id=-100123,
        )

        self.assertEqual(result, (1, 2))
        self.assertEqual(session.execute.call_count, 2)
        self.assertEqual(session.query.call_count, 2)


class TestHandleGroupDeleteOutgoing(unittest.IsolatedAsyncioTestCase):
    async def test_success_deletes_active_database_records(self) -> None:
        @asynccontextmanager
        async def unlocked():
            yield

        event = SimpleNamespace(
            is_private=False,
            raw_text="/delete confirm",
            chat_id=-100123,
            client=MagicMock(),
            delete=AsyncMock(),
        )
        cfg = MagicMock(club_key="round_table", link_club_id=2)

        with (
            patch(
                "bot.services.mtproto_group_delete._resolve_club_id_for_delete",
                return_value=2,
            ),
            patch(
                "bot.services.mtproto_group_delete.get_mtproto_lock",
                return_value=unlocked(),
            ),
            patch(
                "bot.services.mtproto_group_delete.erase_group_chat",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "bot.services.mtproto_group_delete.delete_active_support_group_records",
                return_value=(1, 1),
            ) as cleanup,
        ):
            await handle_group_delete_outgoing(
                event,
                cfg,
                listener_label="test",
            )

        cleanup.assert_called_once_with(
            club_id=2,
            club_key="round_table",
            telegram_chat_id=-100123,
        )


class TestBotApiDeleteHandlerSkipsConfirm(unittest.IsolatedAsyncioTestCase):
    async def test_delete_confirm_does_not_reply_as_missing_custom_command(self) -> None:
        message = MagicMock()
        message.text = "/delete confirm"
        message.reply_text = AsyncMock()
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=111),
        )
        context = SimpleNamespace(args=["confirm"])

        with patch("bot.handlers.commands.is_club_primary_owner", return_value=True):
            await delete_handler(update, context)

        message.reply_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
