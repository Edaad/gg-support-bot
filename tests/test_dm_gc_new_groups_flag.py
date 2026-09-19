"""Tests for GC_DM_GC_NEW_GROUPS_ENABLED (reuse-only auto /gc mode)."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telethon.tl.types import User

from club_gc_settings import is_dm_gc_new_groups_enabled
from bot.services.mtproto_dm_gc_listener import (
    _flow_new_group,
    _run_gc_flow_for_player,
    _run_gc_flow_for_player_client,
    _run_referral_gc_on_listener_loop,
    get_dm_gc_listener_status,
)
from bot.services.player_support_dm_messages import (
    PLAYER_EXISTING_INVITE_MESSAGE,
    PLAYER_INVITE_FALLBACK_MESSAGE,
)


class TestIsDmGcNewGroupsEnabled(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_default_on_when_listener_on(self) -> None:
        with patch("club_gc_settings.is_mtproto_enabled", return_value=True):
            self.assertTrue(is_dm_gc_new_groups_enabled())

    @patch.dict(os.environ, {"GC_DM_GC_NEW_GROUPS_ENABLED": "false"})
    def test_off_when_env_false(self) -> None:
        with patch("club_gc_settings.is_mtproto_enabled", return_value=True):
            self.assertFalse(is_dm_gc_new_groups_enabled())

    @patch.dict(os.environ, {}, clear=True)
    def test_off_when_listener_disabled(self) -> None:
        with patch("club_gc_settings.is_mtproto_enabled", return_value=False):
            self.assertFalse(is_dm_gc_new_groups_enabled())


class TestRunGcFlowForPlayer(unittest.IsolatedAsyncioTestCase):
    async def _run_with_mocks(
        self,
        *,
        existing_row: object | None,
        new_groups_enabled: bool,
    ) -> tuple[AsyncMock, AsyncMock]:
        cfg = MagicMock(club_key="round_table")
        player = MagicMock()
        player.id = 999
        player.username = "player1"
        player.first_name = "Test"
        player.last_name = "Player"

        event = MagicMock()
        event.client = MagicMock()
        event.delete = AsyncMock()

        mock_existing = AsyncMock()
        mock_new = AsyncMock()

        with (
            patch(
                "bot.services.mtproto_dm_gc_listener.try_pg_advisory_lock_club_player",
                return_value=(None, True),
            ),
            patch("bot.services.mtproto_dm_gc_listener.pg_advisory_unlock_session"),
            patch(
                "bot.services.mtproto_dm_gc_listener.fetch_support_group_chat_by_club_player",
                return_value=existing_row,
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener.is_dm_gc_new_groups_enabled",
                return_value=new_groups_enabled,
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener._flow_existing_group",
                mock_existing,
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener._flow_new_group",
                mock_new,
            ),
        ):
            await _run_gc_flow_for_player(
                event,
                cfg,
                player,
                None,
                None,
                listener_label="@rt [id=1]",
                trigger="incoming_dm",
            )
        return mock_existing, mock_new

    async def test_existing_row_uses_existing_flow(self) -> None:
        row = MagicMock()
        mock_existing, mock_new = await self._run_with_mocks(
            existing_row=row,
            new_groups_enabled=False,
        )
        mock_existing.assert_awaited_once()
        mock_new.assert_not_awaited()

    async def test_no_row_and_flag_on_creates_new_group(self) -> None:
        mock_existing, mock_new = await self._run_with_mocks(
            existing_row=None,
            new_groups_enabled=True,
        )
        mock_existing.assert_not_awaited()
        mock_new.assert_awaited_once()

    async def test_no_row_and_flag_off_skips_new_group(self) -> None:
        mock_existing, mock_new = await self._run_with_mocks(
            existing_row=None,
            new_groups_enabled=False,
        )
        mock_existing.assert_not_awaited()
        mock_new.assert_not_awaited()


class TestReferralGcFlow(unittest.IsolatedAsyncioTestCase):
    async def test_created_group_without_direct_add_returns_invite_copy(self) -> None:
        client = MagicMock()
        client.get_me = AsyncMock(return_value=SimpleNamespace(id=100))
        client.get_entity = AsyncMock(return_value=MagicMock())
        client.send_message = AsyncMock()
        cfg = MagicMock(
            club_key="creator_club",
            link_club_id=3,
            mtproto_session="creator",
            group_photo_path=None,
        )
        player = MagicMock(
            id=555,
            username="player",
            first_name="Test",
            last_name="Player",
        )
        outcome = SimpleNamespace(
            telegram_chat_id=-100123,
            telegram_chat_title="CC / / @player",
            invite_link="https://t.me/+initial",
            player_direct_add_ok=False,
            warnings=[],
            error_hint=None,
            added_users=[],
            failed_users=[],
            link_joined_users=[],
            promoted_admins=[],
            link_join_failures=[],
            initial_message_sent=True,
        )

        with (
            patch(
                "bot.services.mtproto_dm_gc_listener.create_support_group",
                new=AsyncMock(return_value=outcome),
            ),
            patch(
                "bot.services.group_chat_invite_links.resolve_support_group_invite_link",
                new=AsyncMock(return_value=("https://t.me/+fresh", "mtproto_export")),
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener.persist_support_group_chat_row",
                return_value=(1, None),
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener.ensure_group_chat_linked",
                return_value=True,
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener.send_post_gc_intro_bundle",
                new=AsyncMock(),
            ),
            patch(
                "bot.services.escalation_notification.notify_new_player_onboarded",
                new=AsyncMock(),
            ),
        ):
            text = await _flow_new_group(
                client,
                cfg,
                player,
                "PlayGGSupport",
                MagicMock(),
                listener_label="creator_club:referral",
                trigger="referral_link",
                send_player_dm=False,
            )

        self.assertEqual(
            text,
            PLAYER_INVITE_FALLBACK_MESSAGE.format(invite_link="https://t.me/+fresh"),
        )
        client.send_message.assert_not_awaited()

    async def test_existing_group_returns_bot_copy_without_admin_dm(self) -> None:
        client = MagicMock()
        cfg = MagicMock(club_key="creator_club")
        player = MagicMock(id=555, username="player")
        row = MagicMock()
        existing_flow = AsyncMock(return_value="https://t.me/+existing")

        with (
            patch(
                "bot.services.mtproto_dm_gc_listener.try_pg_advisory_lock_club_player",
                return_value=(None, True),
            ),
            patch("bot.services.mtproto_dm_gc_listener.pg_advisory_unlock_session"),
            patch(
                "bot.services.mtproto_dm_gc_listener.fetch_support_group_chat_by_club_player",
                return_value=row,
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener._flow_existing_group",
                existing_flow,
            ),
        ):
            text = await _run_gc_flow_for_player_client(
                client,
                cfg,
                player,
                "PlayGGSupport",
                MagicMock(),
                listener_label="creator_club:referral",
                trigger="referral_link",
                send_player_dm=False,
            )

        self.assertEqual(
            text,
            PLAYER_EXISTING_INVITE_MESSAGE.format(invite_link="https://t.me/+existing"),
        )
        self.assertFalse(existing_flow.await_args.kwargs["send_player_dm"])

    async def test_new_group_returns_existing_success_template(self) -> None:
        client = MagicMock()
        cfg = MagicMock(club_key="clubgto")
        player = MagicMock(id=555, username="player")
        new_flow = AsyncMock(return_value="created GC success copy")

        with (
            patch(
                "bot.services.mtproto_dm_gc_listener.try_pg_advisory_lock_club_player",
                return_value=(None, True),
            ),
            patch("bot.services.mtproto_dm_gc_listener.pg_advisory_unlock_session"),
            patch(
                "bot.services.mtproto_dm_gc_listener.fetch_support_group_chat_by_club_player",
                return_value=None,
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener.is_dm_gc_new_groups_enabled",
                return_value=True,
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener._flow_new_group",
                new_flow,
            ),
        ):
            text = await _run_gc_flow_for_player_client(
                client,
                cfg,
                player,
                "PlayGGSupport",
                MagicMock(),
                listener_label="clubgto:referral",
                trigger="referral_link",
                send_player_dm=False,
            )

        self.assertEqual(text, "created GC success copy")
        self.assertFalse(new_flow.await_args.kwargs["send_player_dm"])

    async def test_unresolved_player_returns_fallback_signal(self) -> None:
        cfg = MagicMock(club_key="creator_club")
        client = MagicMock()
        client.is_connected.return_value = True
        client.get_entity = AsyncMock(side_effect=ValueError("not found"))

        with (
            patch(
                "bot.services.mtproto_dm_gc_listener.get_club_gc_config_by_link_club_id",
                return_value=cfg,
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener.get_listener_client",
                return_value=client,
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener._run_gc_flow_for_player_client",
                new_callable=AsyncMock,
            ) as flow,
        ):
            text = await _run_referral_gc_on_listener_loop(
                club_id=3,
                player_telegram_user_id=555,
                player_username=None,
            )

        self.assertIsNone(text)
        flow.assert_not_awaited()

    async def test_resolved_player_runs_on_live_listener(self) -> None:
        cfg = MagicMock(club_key="creator_club")
        player = User(id=555, first_name="Test", username="player")
        client = MagicMock()
        client.is_connected.return_value = True
        client.get_entity = AsyncMock(return_value=player)

        with (
            patch(
                "bot.services.mtproto_dm_gc_listener.get_club_gc_config_by_link_club_id",
                return_value=cfg,
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener.get_listener_client",
                return_value=client,
            ),
            patch(
                "bot.services.mtproto_dm_gc_listener._run_gc_flow_for_player_client",
                new_callable=AsyncMock,
                return_value="success",
            ) as flow,
        ):
            text = await _run_referral_gc_on_listener_loop(
                club_id=3,
                player_telegram_user_id=555,
                player_username="player",
            )

        self.assertEqual(text, "success")
        self.assertFalse(flow.await_args.kwargs["send_player_dm"])


class TestListenerStatus(unittest.TestCase):
    @patch.dict(os.environ, {"GC_DM_GC_NEW_GROUPS_ENABLED": "false"})
    def test_status_includes_new_groups_enabled(self) -> None:
        with patch("club_gc_settings.is_mtproto_enabled", return_value=True):
            status = get_dm_gc_listener_status()
        self.assertIn("new_groups_enabled", status)
        self.assertFalse(status["new_groups_enabled"])


if __name__ == "__main__":
    unittest.main()
