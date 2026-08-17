"""Unit tests for member-join intro suppression during migration recovery."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers import groups
from bot.services import watched_group_escalation as wge

CHAT_ID = -1003902137688


def _make_context() -> MagicMock:
    context = MagicMock()
    context.bot = MagicMock()
    return context


class TestShouldSkipClubOnboarding(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop(wge.WATCH_GROUP_ESCALATION_CHAT_IDS_ENV, None)

    def test_ops_title_allows(self) -> None:
        self.assertFalse(
            groups.should_skip_club_onboarding(
                1, "Round Table Support & GG Support"
            )
        )

    def test_valid_gc_title_allows(self) -> None:
        self.assertFalse(
            groups.should_skip_club_onboarding(1, "RT / 8190-5287 / Player")
        )

    def test_empty_player_id_gc_title_allows(self) -> None:
        self.assertFalse(
            groups.should_skip_club_onboarding(1, "RT / / @username")
        )
        self.assertFalse(
            groups.should_skip_club_onboarding(1, "CC / / John")
        )
        self.assertFalse(
            groups.should_skip_club_onboarding(1, "GTO / / @ho3ennn")
        )

    def test_allowlisted_skips_even_with_gc_title(self) -> None:
        os.environ[wge.WATCH_GROUP_ESCALATION_CHAT_IDS_ENV] = "42"
        self.assertTrue(
            groups.should_skip_club_onboarding(42, "RT / 8190-5287 / Player")
        )


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


class TestOnMyChatMemberOnboarding(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        groups._join_intro_sent_at.clear()
        groups._member_join_bundle_until.clear()
        groups._post_gc_recent_until.clear()

    @patch("bot.handlers.groups.set_group_club", return_value=2)
    @patch("bot.handlers.groups.is_group_linked", return_value=False)
    @patch("bot.handlers.groups._send_member_join_preamble_and_pdf", new_callable=AsyncMock)
    @patch("bot.handlers.groups.get_club_welcome", return_value=None)
    @patch("bot.handlers.groups.bind_chat_from_title")
    async def test_ops_title_runs_onboarding(
        self,
        bind: MagicMock,
        _welcome: MagicMock,
        preamble: AsyncMock,
        _linked: MagicMock,
        set_club: MagicMock,
    ) -> None:
        bind.return_value = MagicMock(
            ok=False, gg_player_id=None, error="Invalid group name format."
        )
        preamble.return_value = -1001
        update = MagicMock()
        update.effective_chat.type = "group"
        update.effective_chat.id = -1001
        update.effective_chat.title = "Round Table Support & GG Support"
        update.effective_user.id = 493310710
        update.my_chat_member.old_chat_member.status = "left"
        update.my_chat_member.new_chat_member.status = "member"

        context = _make_context()
        await groups.on_my_chat_member_updated(update, context)

        set_club.assert_called_once()
        preamble.assert_awaited_once()

    @patch("bot.handlers.groups.set_group_club", return_value=2)
    @patch("bot.handlers.groups.is_group_linked", return_value=False)
    @patch("bot.handlers.groups._send_member_join_preamble_and_pdf", new_callable=AsyncMock)
    @patch("bot.handlers.groups.get_club_welcome", return_value=None)
    @patch("bot.handlers.groups.bind_chat_from_title")
    async def test_empty_player_id_gc_title_runs_onboarding(
        self,
        bind: MagicMock,
        _welcome: MagicMock,
        preamble: AsyncMock,
        _linked: MagicMock,
        set_club: MagicMock,
    ) -> None:
        bind.return_value = MagicMock(
            ok=False, gg_player_id=None, error="Invalid group name format."
        )
        preamble.return_value = -1002
        update = MagicMock()
        update.effective_chat.type = "group"
        update.effective_chat.id = -1002
        update.effective_chat.title = "RT / / @username"
        update.effective_user.id = 493310710
        update.my_chat_member.old_chat_member.status = "left"
        update.my_chat_member.new_chat_member.status = "member"

        context = _make_context()
        await groups.on_my_chat_member_updated(update, context)

        set_club.assert_called_once()
        preamble.assert_awaited_once()


class TestBotCallChatMigration(unittest.IsolatedAsyncioTestCase):
    @patch("bot.services.chat_id_remap.try_silent_supergroup_remap")
    async def test_retries_on_chat_migrated(self, remap: MagicMock) -> None:
        from telegram.error import ChatMigrated

        bot = MagicMock()
        calls: list[int] = []

        async def send(cid: int) -> None:
            calls.append(int(cid))
            if cid == -555:
                raise ChatMigrated(-100555)

        live = await groups._bot_call_chat(bot, -555, send)
        self.assertEqual(live, -100555)
        self.assertEqual(calls, [-555, -100555])
        remap.assert_called_once_with(-555, -100555)


if __name__ == "__main__":
    unittest.main()
