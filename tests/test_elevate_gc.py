"""Tests for Elevate Admin group creation (link-join + admin promote)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telethon.tl.types import Chat, User

from bot.services.mtproto_group_join import parse_invite_hash
from club_gc_settings import (
    ClubGcConfig,
    build_auxiliary_mtproto_config,
    build_club_gc_config,
    link_join_exclude_normalized,
    resolve_group_creator_cfg,
)


class TestParseInviteHash(unittest.TestCase):
    def test_t_me_plus(self) -> None:
        self.assertEqual(
            parse_invite_hash("https://t.me/+AbCdEfGhIj"),
            "AbCdEfGhIj",
        )

    def test_t_me_joinchat(self) -> None:
        self.assertEqual(
            parse_invite_hash("https://t.me/joinchat/XYZ123"),
            "XYZ123",
        )

    def test_bare_plus(self) -> None:
        self.assertEqual(parse_invite_hash("+HashOnly"), "HashOnly")

    def test_invalid(self) -> None:
        self.assertIsNone(parse_invite_hash("https://t.me/notaninvite"))
        self.assertIsNone(parse_invite_hash(""))


class TestElevateConfig(unittest.TestCase):
    def test_elevate_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GC_ELEVATE_CREATOR_ROUND_TABLE", None)
            cfg = build_club_gc_config()["round_table"]
            self.assertIsNone(cfg.group_creator_club_key)
            self.assertEqual(resolve_group_creator_cfg(cfg).club_key, "round_table")

    def test_elevate_enabled_uses_round_table_for_link_join(self) -> None:
        env = {
            "GC_ELEVATE_CREATOR_ROUND_TABLE": "true",
            "GC_PROMOTE_ADMIN_ROUND_TABLE": "@RoundTableSupport2",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = build_club_gc_config()["round_table"]
            self.assertEqual(cfg.group_creator_club_key, "elevate_admin")
            self.assertEqual(cfg.link_join_club_key, "round_table")
            self.assertEqual(cfg.promote_admin_marker, "@RoundTableSupport2")
            exclude = link_join_exclude_normalized(cfg)
            self.assertIn("roundtablesupport2", exclude)
            with patch("club_gc_settings._elevate_creator_round_table_enabled", return_value=True):
                creator = resolve_group_creator_cfg(cfg)
            self.assertEqual(creator.club_key, "elevate_admin")

    def test_auxiliary_profiles_elevate_only(self) -> None:
        aux = build_auxiliary_mtproto_config()
        self.assertIn("elevate_admin", aux)
        self.assertNotIn("round_table_support2", aux)
        self.assertEqual(aux["elevate_admin"].session_role, "creator")


class TestCreateSupportMegagroupElevate(unittest.IsolatedAsyncioTestCase):
    def _listener_cfg(self) -> ClubGcConfig:
        return ClubGcConfig(
            club_key="round_table",
            club_display_name="Round Table",
            command_admin_user_id=1,
            mtproto_session="sessions/round_table.session",
            mtproto_phone_number=None,
            group_title="RT / New Player",
            group_photo_path=None,
            users_to_add=("@RoundTableSupport3",),
            bot_account="@Bot",
            initial_group_message_template="link: {invite_link}",
            link_club_id=2,
            group_creator_club_key="elevate_admin",
            link_join_club_key="round_table",
            promote_admin_marker="@RoundTableSupport2",
            link_join_exclude_markers=("@RoundTableSupport2",),
        )

    def _creator_cfg(self) -> ClubGcConfig:
        return ClubGcConfig(
            club_key="elevate_admin",
            club_display_name="Elevate Admin",
            command_admin_user_id=0,
            mtproto_session="sessions/elevate_admin.session",
            mtproto_phone_number=None,
            group_title="",
            group_photo_path=None,
            users_to_add=(),
            bot_account=None,
            initial_group_message_template="",
            link_club_id=0,
            session_role="creator",
        )

    @patch("club_gc_settings._elevate_creator_round_table_enabled", return_value=True)
    @patch("bot.services.mtproto_group_create.resolve_link_join_cfg")
    @patch("bot.services.mtproto_group_create.resolve_group_creator_cfg")
    @patch("bot.services.mtproto_group_join.run_link_join_and_promote", new_callable=AsyncMock)
    @patch("bot.services.mtproto_group_create.make_client")
    @patch("bot.services.mtproto_group_create.get_mtproto_lock")
    async def test_link_join_and_promote_after_create(
        self,
        mock_lock: MagicMock,
        mock_make_client: MagicMock,
        mock_link_promote: AsyncMock,
        mock_resolve_creator: MagicMock,
        mock_resolve_link: MagicMock,
        _mock_elevate_flag: MagicMock,
    ) -> None:
        from bot.services.mtproto_group_create import create_support_group

        listener_cfg = self._listener_cfg()
        creator_cfg = self._creator_cfg()
        listener_client = MagicMock()
        listener_client.is_connected = MagicMock(return_value=True)

        mock_resolve_creator.return_value = creator_cfg
        mock_resolve_link.return_value = listener_cfg
        mock_lock.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_lock.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_link_promote.return_value = (
            [{"user": "@RoundTableSupport2", "kind": "link_join"}],
            [{"user": "@RoundTableSupport2", "kind": "admin"}],
            [],
        )

        seed_user = MagicMock(spec=User)
        seed_user.id = 111

        chat = MagicMock(spec=Chat)
        chat.title = "RT / / @player"
        chat.id = 999
        created = MagicMock()
        created.chats = [chat]

        group_ent = MagicMock(spec=Chat)
        group_ent.id = 999
        group_ent.title = "RT / / @player"

        client = MagicMock()
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()
        client.is_user_authorized = AsyncMock(return_value=True)
        client.get_entity = AsyncMock(return_value=group_ent)
        client.send_message = AsyncMock()

        async def flood_retry(tag: str, factory):
            if tag == "CreateChatRequest":
                return created
            result = factory()
            if hasattr(result, "__await__"):
                return await result
            return result

        async def export_link(_peer):
            return "https://t.me/+TestHash"

        client.export_chat_invite_link = export_link
        mock_make_client.return_value = client

        with (
            patch(
                "bot.services.mtproto_group_create._with_single_flood_retry",
                new_callable=AsyncMock,
                side_effect=flood_retry,
            ),
            patch(
                "bot.services.mtproto_group_create._resolve_seed_user_for_create_chat",
                new_callable=AsyncMock,
                return_value=seed_user,
            ),
            patch(
                "bot.services.mtproto_group_create._invite_one",
                new_callable=AsyncMock,
                return_value=(True, None),
            ),
            patch(
                "bot.services.mtproto_group_create.apply_club_group_photo",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "bot.services.mtproto_group_create.get_gc_users_to_add",
                return_value=("@RoundTableSupport3",),
            ),
            patch("bot.handlers.groups._mark_post_gc_bundle_window"),
            patch("telethon.utils.get_peer_id", return_value=-5287778428),
        ):
            outcome = await create_support_group(
                listener_cfg,
                bot_dm_username="Bot",
                link_join_client=listener_client,
            )

        mock_link_promote.assert_awaited_once()
        call_kw = mock_link_promote.await_args.kwargs
        self.assertIs(call_kw.get("link_join_client"), listener_client)
        self.assertEqual(outcome.link_joined_users[0]["kind"], "link_join")
        self.assertEqual(outcome.promoted_admins[0]["kind"], "admin")
        invite_markers = [u["user"] for u in outcome.added_users]
        self.assertNotIn("@RoundTableSupport2", invite_markers)


class TestRunLinkJoinAndPromote(unittest.IsolatedAsyncioTestCase):
    @patch("bot.services.mtproto_group_join.promote_group_admin", new_callable=AsyncMock)
    @patch("bot.services.mtproto_group_join.join_chat_via_invite_link", new_callable=AsyncMock)
    @patch("bot.services.mtproto_group_join.make_client")
    @patch("bot.services.mtproto_group_join.get_mtproto_lock")
    async def test_promote_uses_creator_channel_entity(
        self,
        mock_lock: MagicMock,
        mock_make_client: MagicMock,
        mock_join: AsyncMock,
        mock_promote: AsyncMock,
    ) -> None:
        from bot.services.mtproto_group_join import run_link_join_and_promote

        mock_lock.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_lock.return_value.__aexit__ = AsyncMock(return_value=None)

        creator_group = MagicMock(name="creator_group")
        join_group = MagicMock(name="join_group")
        mock_join.return_value = (join_group, None)
        mock_promote.return_value = (True, None)

        join_client = MagicMock()
        join_client.is_connected = MagicMock(return_value=True)
        join_client.is_user_authorized = AsyncMock(return_value=True)

        promote_client = MagicMock()
        promote_client.connect = AsyncMock()
        promote_client.disconnect = AsyncMock()
        promote_client.is_user_authorized = AsyncMock(return_value=True)
        mock_make_client.return_value = promote_client

        creator_cfg = MagicMock(club_key="elevate_admin")
        link_join_cfg = MagicMock(club_key="round_table")

        lj, prom, fail = await run_link_join_and_promote(
            creator_cfg,
            group_entity=creator_group,
            invite_link="https://t.me/+TestHash",
            promote_marker="@RoundTableSupport2",
            link_join_cfg=link_join_cfg,
            link_join_client=join_client,
        )

        self.assertEqual(lj, [{"user": "@RoundTableSupport2", "kind": "link_join"}])
        self.assertEqual(prom, [{"user": "@RoundTableSupport2", "kind": "admin"}])
        self.assertEqual(fail, [])
        mock_promote.assert_awaited_once()
        promote_args = mock_promote.await_args
        self.assertIs(promote_args.args[1], creator_group)
        self.assertIsNot(promote_args.args[1], join_group)


if __name__ == "__main__":
    unittest.main()
