"""Unit tests for member-join intro suppression during migration recovery."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers import groups

CHAT_ID = -1003902137688


def _make_context() -> MagicMock:
    context = MagicMock()
    context.bot = MagicMock()
    return context


class TestMaybeSendMemberJoinIntro(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        groups._join_intro_sent_at.clear()
        groups._member_join_bundle_until.clear()
        groups._post_gc_recent_until.clear()

    @patch("bot.handlers.groups.get_club_for_chat", return_value=None)
    async def test_no_club_skips(self, _mock_club: MagicMock) -> None:
        context = _make_context()

        await groups._maybe_send_member_join_intro(context, CHAT_ID)

        context.bot.send_message.assert_not_called()

    @patch("bot.handlers.groups._deliver_member_join_intro_messages", new_callable=AsyncMock)
    @patch("bot.services.migration_recovery.is_migrated_recovery_chat", return_value=True)
    @patch("club_gc_settings.is_migration_recovery_skip_welcome_enabled", return_value=True)
    @patch("bot.handlers.groups.get_club_for_chat", return_value=4)
    async def test_skips_when_skip_welcome_on_and_chat_in_table(
        self,
        _mock_club: MagicMock,
        _mock_skip_on: MagicMock,
        _mock_in_table: MagicMock,
        mock_deliver: AsyncMock,
    ) -> None:
        context = _make_context()

        await groups._maybe_send_member_join_intro(context, CHAT_ID)

        mock_deliver.assert_not_awaited()

    @patch("bot.handlers.groups._deliver_member_join_intro_messages", new_callable=AsyncMock)
    @patch("bot.services.migration_recovery.is_migrated_recovery_chat", return_value=True)
    @patch("club_gc_settings.is_migration_recovery_skip_welcome_enabled", return_value=False)
    @patch("bot.handlers.groups.get_club_for_chat", return_value=4)
    async def test_sends_when_skip_welcome_off_even_if_chat_in_table(
        self,
        _mock_club: MagicMock,
        _mock_skip_on: MagicMock,
        _mock_in_table: MagicMock,
        mock_deliver: AsyncMock,
    ) -> None:
        context = _make_context()

        await groups._maybe_send_member_join_intro(context, CHAT_ID)

        mock_deliver.assert_awaited_once_with(CHAT_ID, 4, context.bot)

    @patch("bot.handlers.groups._deliver_member_join_intro_messages", new_callable=AsyncMock)
    @patch("bot.services.migration_recovery.is_migrated_recovery_chat", return_value=False)
    @patch("club_gc_settings.is_migration_recovery_skip_welcome_enabled", return_value=True)
    @patch("bot.handlers.groups.get_club_for_chat", return_value=4)
    async def test_sends_when_skip_welcome_on_but_chat_not_in_table(
        self,
        _mock_club: MagicMock,
        _mock_skip_on: MagicMock,
        _mock_in_table: MagicMock,
        mock_deliver: AsyncMock,
    ) -> None:
        context = _make_context()

        await groups._maybe_send_member_join_intro(context, CHAT_ID)

        mock_deliver.assert_awaited_once_with(CHAT_ID, 4, context.bot)


if __name__ == "__main__":
    unittest.main()
