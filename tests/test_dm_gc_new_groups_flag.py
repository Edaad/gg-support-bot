"""Tests for GC_DM_GC_NEW_GROUPS_ENABLED (reuse-only auto /gc mode)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from club_gc_settings import is_dm_gc_new_groups_enabled
from bot.services.mtproto_dm_gc_listener import (
    _run_gc_flow_for_player,
    get_dm_gc_listener_status,
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


class TestListenerStatus(unittest.TestCase):
    @patch.dict(os.environ, {"GC_DM_GC_NEW_GROUPS_ENABLED": "false"})
    def test_status_includes_new_groups_enabled(self) -> None:
        with patch("club_gc_settings.is_mtproto_enabled", return_value=True):
            status = get_dm_gc_listener_status()
        self.assertIn("new_groups_enabled", status)
        self.assertFalse(status["new_groups_enabled"])


if __name__ == "__main__":
    unittest.main()
