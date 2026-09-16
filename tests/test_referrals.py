"""Tests for referral deep links (track + ack)."""

from __future__ import annotations

import unittest
from os import environ
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers import referral as referral_handler
from bot.services import referrals as ref
from club_gc_settings import build_club_gc_config
from db.models import ReferralLink


def _link(
    *,
    id: int = 1,
    code: str = "ref_AbCdEfGhIjKl",
    club_id: int = 4,
    referrer_gg_player_id: str = "1111-2222",
    referrer_chat_id: int = -1001,
):
    return SimpleNamespace(
        id=id,
        code=code,
        club_id=club_id,
        referrer_gg_player_id=referrer_gg_player_id,
        referrer_chat_id=referrer_chat_id,
    )


def _attr(
    *,
    id: int = 10,
    referral_link_id: int = 1,
    club_id: int = 4,
    clicker_telegram_user_id: int = 555,
    referred_gg_player_id: str | None = None,
    referred_chat_id: int | None = None,
    status: str = ref.STATUS_PENDING,
):
    return SimpleNamespace(
        id=id,
        referral_link_id=referral_link_id,
        club_id=club_id,
        clicker_telegram_user_id=clicker_telegram_user_id,
        referred_gg_player_id=referred_gg_player_id,
        referred_chat_id=referred_chat_id,
        status=status,
        credited_at=None,
        acked_at=None,
        updated_at=None,
    )


class ReferralHelpersTest(unittest.TestCase):
    def test_generate_code_shape(self):
        code = ref.generate_referral_code()
        self.assertTrue(ref.is_referral_start_payload(code))
        self.assertEqual(len(code), len(ref.CODE_PREFIX) + ref.CODE_BODY_LEN)

    def test_is_referral_start_payload_rejects_bad(self):
        self.assertFalse(ref.is_referral_start_payload(None))
        self.assertFalse(ref.is_referral_start_payload("ref_short"))
        self.assertFalse(ref.is_referral_start_payload("ref_!!!!!!!!!!!!"))
        self.assertFalse(ref.is_referral_start_payload("start_AbCdEfGhIjKl"))

    def test_build_url_and_message(self):
        url = ref.build_referral_url(bot_username="PlayGGSupport", code="ref_AbCdEfGhIjKl")
        self.assertEqual(url, "https://t.me/PlayGGSupport?start=ref_AbCdEfGhIjKl")
        msg = ref.format_referral_link_message(url)
        self.assertIn("unique referral link", msg)
        self.assertIn("exclusive rewards!!", msg)
        self.assertIn(url, msg)

    def test_hop_and_existing_copy(self):
        with patch.object(ref, "support_account_username", return_value="@ClubGTOAdmin"):
            self.assertEqual(
                ref.hop_message(4),
                "Message @ClubGTOAdmin to get your support group.",
            )
        self.assertEqual(
            ref.existing_player_message("https://t.me/+abc"),
            "You already have a support group. Message us there.\n\nhttps://t.me/+abc",
        )
        self.assertEqual(
            ref.existing_player_message(None),
            "You already have a support group. Message us there.",
        )

    def test_dedicated_fallback_accounts(self):
        env_keys = {
            "REFERRAL_SUPPORT_ACCOUNT_ROUND_TABLE",
            "REFERRAL_SUPPORT_ACCOUNT_CREATOR_CLUB",
            "REFERRAL_SUPPORT_ACCOUNT_CLUB_GTO",
        }
        clean_env = {key: value for key, value in environ.items() if key not in env_keys}
        with patch.dict(environ, clean_env, clear=True):
            configs = build_club_gc_config()

        self.assertEqual(
            configs["round_table"].referral_support_account,
            "@RoundTableSupport2",
        )
        self.assertEqual(
            configs["creator_club"].referral_support_account,
            "@CreatorClubSupport2",
        )
        self.assertEqual(
            configs["clubgto"].referral_support_account,
            "@ClubGTOAdmin",
        )

    def test_format_my_referrals(self):
        self.assertEqual(
            ref.format_my_referrals_messages(
                ["5323-5255", "8190-5287", "2342-4223"]
            ),
            [
                "You have referred 3 players:\n\n"
                "• 5323-5255\n"
                "• 8190-5287\n"
                "• 2342-4223"
            ],
        )
        self.assertEqual(
            ref.format_my_referrals_messages([]),
            ["You haven't referred any players yet."],
        )

    def test_format_my_referrals_splits_without_truncating(self):
        player_ids = [f"{i:04d}-{i:04d}" for i in range(20)]
        messages = ref.format_my_referrals_messages(player_ids, max_chars=80)
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(message) <= 80 for message in messages))
        combined = "\n".join(messages)
        for player_id in player_ids:
            self.assertIn(f"• {player_id}", combined)


class MyReferralsQueryTest(unittest.TestCase):
    @patch.object(ref, "get_db")
    def test_returns_current_credited_ids_in_query_order(self, mock_get_db):
        rows = [
            SimpleNamespace(referred_gg_player_id="5323-5255"),
            SimpleNamespace(referred_gg_player_id="8190-5287"),
        ]
        session = MagicMock()
        query = session.query.return_value
        query.join.return_value.filter.return_value.order_by.return_value.all.return_value = (
            rows
        )
        mock_get_db.return_value.__enter__.return_value = session
        mock_get_db.return_value.__exit__.return_value = False

        self.assertEqual(
            ref.get_credited_referral_player_ids(
                club_id=4, referrer_chat_id=-1001
            ),
            ["5323-5255", "8190-5287"],
        )
        query.join.return_value.filter.assert_called_once()


class EnsureReferralLinkTest(unittest.TestCase):
    @patch.object(ref, "get_db")
    def test_returns_existing_stable_code(self, mock_get_db):
        existing = _link(code="ref_StableCode12")
        session = MagicMock()
        session.query.return_value.filter.return_value.one_or_none.return_value = existing
        mock_get_db.return_value.__enter__.return_value = session
        mock_get_db.return_value.__exit__.return_value = False

        out = ref.ensure_referral_link(
            club_id=4,
            referrer_chat_id=-1001,
            referrer_gg_player_id="1111-2222",
        )
        self.assertIs(out, existing)
        self.assertEqual(out.code, "ref_StableCode12")
        session.add.assert_not_called()

    @patch.object(ref, "get_db")
    def test_retitle_updates_player_id_keeps_code(self, mock_get_db):
        existing = _link(
            code="ref_StableCode12", referrer_gg_player_id="1111-2222"
        )
        session = MagicMock()
        session.query.return_value.filter.return_value.one_or_none.return_value = existing
        mock_get_db.return_value.__enter__.return_value = session
        mock_get_db.return_value.__exit__.return_value = False

        out = ref.ensure_referral_link(
            club_id=4,
            referrer_chat_id=-1001,
            referrer_gg_player_id="9999-8888",
        )
        self.assertEqual(out.code, "ref_StableCode12")
        self.assertEqual(out.referrer_gg_player_id, "9999-8888")
        session.flush.assert_called()


class HandleStartPayloadTest(unittest.TestCase):
    @patch.object(ref, "fetch_support_group_chat_by_club_player", return_value=None)
    @patch.object(ref, "get_link_by_code")
    @patch.object(ref, "get_db")
    @patch.object(ref, "_club_key_for_club_id", return_value="clubgto")
    @patch.object(ref, "support_account_username", return_value="@ClubGTOAdmin")
    def test_first_click_creates_pending(
        self, _user, _key, mock_get_db, mock_link, _sgc
    ):
        mock_link.return_value = _link()
        session = MagicMock()
        session.query.return_value.filter.return_value.one_or_none.return_value = None
        mock_get_db.return_value.__enter__.return_value = session
        mock_get_db.return_value.__exit__.return_value = False

        result = ref.handle_start_payload(
            clicker_telegram_user_id=555, code="ref_AbCdEfGhIjKl"
        )
        self.assertEqual(result.kind, "hop")
        self.assertIn("@ClubGTOAdmin", result.text)
        session.add.assert_called_once()

    @patch.object(ref, "fetch_support_group_chat_by_club_player", return_value=None)
    @patch.object(ref, "get_link_by_code")
    @patch.object(ref, "get_db")
    @patch.object(ref, "_club_key_for_club_id", return_value="clubgto")
    @patch.object(ref, "support_account_username", return_value="@ClubGTOAdmin")
    def test_second_click_ignored(
        self, _user, _key, mock_get_db, mock_link, _sgc
    ):
        mock_link.return_value = _link(id=2, code="ref_SecondLink01")
        already = _attr(referral_link_id=1)
        session = MagicMock()
        session.query.return_value.filter.return_value.one_or_none.return_value = already
        mock_get_db.return_value.__enter__.return_value = session
        mock_get_db.return_value.__exit__.return_value = False

        result = ref.handle_start_payload(
            clicker_telegram_user_id=555, code="ref_SecondLink01"
        )
        self.assertEqual(result.kind, "hop")
        session.add.assert_not_called()

    @patch.object(ref, "fetch_support_group_chat_by_club_player")
    @patch.object(ref, "get_link_by_code")
    @patch.object(ref, "_club_key_for_club_id", return_value="clubgto")
    def test_existing_player_no_attribution(self, _key, mock_link, mock_sgc):
        mock_link.return_value = _link()
        mock_sgc.return_value = SimpleNamespace(invite_link="https://t.me/+xyz")
        result = ref.handle_start_payload(
            clicker_telegram_user_id=555, code="ref_AbCdEfGhIjKl"
        )
        self.assertEqual(result.kind, "existing")
        self.assertIn("https://t.me/+xyz", result.text)


class ReferralStartHandlerTest(unittest.IsolatedAsyncioTestCase):
    @patch.object(
        referral_handler,
        "handle_start_payload",
        return_value=ref.StartResult(
            kind="hop",
            text="fallback",
            club_id=3,
        ),
    )
    @patch(
        "bot.services.mtproto_dm_gc_listener.run_referral_gc_for_bot_user",
        new_callable=AsyncMock,
        return_value="existing GC success copy",
    )
    async def test_explicit_link_returns_automated_success(
        self, mock_auto, _mock_start
    ):
        update = SimpleNamespace(
            message=SimpleNamespace(reply_text=AsyncMock()),
            effective_chat=SimpleNamespace(type="private"),
            effective_user=SimpleNamespace(id=555, username="player"),
        )
        context = SimpleNamespace(args=["ref_AbCdEfGhIjKl"])

        handled = await referral_handler.maybe_handle_referral_start(update, context)

        self.assertTrue(handled)
        mock_auto.assert_awaited_once_with(
            club_id=3,
            player_telegram_user_id=555,
            player_username="player",
        )
        update.message.reply_text.assert_awaited_once_with(
            "existing GC success copy"
        )

    @patch.object(
        referral_handler,
        "handle_start_payload",
        return_value=ref.StartResult(
            kind="existing",
            text="old existing copy",
            club_id=3,
        ),
    )
    @patch.object(
        referral_handler,
        "hop_message",
        return_value="Message @CreatorClubSupport2 to get your support group.",
    )
    @patch(
        "bot.services.mtproto_dm_gc_listener.run_referral_gc_for_bot_user",
        new_callable=AsyncMock,
        return_value=None,
    )
    async def test_automation_failure_returns_club_fallback(
        self, mock_auto, mock_hop, _mock_start
    ):
        update = SimpleNamespace(
            message=SimpleNamespace(reply_text=AsyncMock()),
            effective_chat=SimpleNamespace(type="private"),
            effective_user=SimpleNamespace(id=555, username=None),
        )
        context = SimpleNamespace(args=["ref_AbCdEfGhIjKl"])

        await referral_handler.maybe_handle_referral_start(update, context)

        mock_auto.assert_awaited_once()
        mock_hop.assert_called_once_with(3)
        update.message.reply_text.assert_awaited_once_with(
            "Message @CreatorClubSupport2 to get your support group."
        )


class OnPlayerIdBoundTest(unittest.TestCase):
    @patch.object(ref, "fetch_support_group_chat_by_telegram_chat_id")
    @patch.object(ref, "get_db")
    def test_first_credit_acks_both_groups(self, mock_get_db, mock_sgc):
        mock_sgc.return_value = SimpleNamespace(player_telegram_user_id=555)
        link = _link(referrer_chat_id=-1001, referrer_gg_player_id="1111-2222")
        attr = _attr(status=ref.STATUS_PENDING, clicker_telegram_user_id=555)

        session = MagicMock()
        link_q = MagicMock()
        link_q.filter.return_value.one_or_none.return_value = None
        link_q.get.return_value = link
        attr_q = MagicMock()
        attr_q.filter.return_value.one_or_none.return_value = attr

        def query_side_effect(model):
            if model is ReferralLink:
                return link_q
            return attr_q

        session.query.side_effect = query_side_effect
        mock_get_db.return_value.__enter__.return_value = session
        mock_get_db.return_value.__exit__.return_value = False

        msgs = ref.on_player_id_bound(
            chat_id=-2002,
            club_id=4,
            gg_player_id="8190-5287",
            previous_gg_player_id=None,
            conflict=False,
        )
        self.assertEqual(attr.status, ref.STATUS_CREDITED)
        self.assertEqual(attr.referred_gg_player_id, "8190-5287")
        self.assertEqual(attr.referred_chat_id, -2002)
        texts = {m.text for m in msgs}
        self.assertIn("Your referral 8190-5287 joined the club.", texts)
        self.assertIn("Referred by 1111-2222.", texts)

    @patch.object(ref, "fetch_support_group_chat_by_telegram_chat_id")
    @patch.object(ref, "get_db")
    def test_referred_retitle_pings_referrer(self, mock_get_db, mock_sgc):
        mock_sgc.return_value = SimpleNamespace(player_telegram_user_id=555)
        link = _link(referrer_chat_id=-1001, referrer_gg_player_id="1111-2222")
        attr = _attr(
            status=ref.STATUS_CREDITED,
            clicker_telegram_user_id=555,
            referred_gg_player_id="8190-5287",
            referred_chat_id=-2002,
        )

        session = MagicMock()
        link_q = MagicMock()
        link_q.filter.return_value.one_or_none.return_value = None
        link_q.get.return_value = link
        attr_q = MagicMock()
        attr_q.filter.return_value.one_or_none.return_value = attr

        def query_side_effect(model):
            if model is ReferralLink:
                return link_q
            return attr_q

        session.query.side_effect = query_side_effect
        mock_get_db.return_value.__enter__.return_value = session
        mock_get_db.return_value.__exit__.return_value = False

        msgs = ref.on_player_id_bound(
            chat_id=-2002,
            club_id=4,
            gg_player_id="5323-5255",
            previous_gg_player_id="8190-5287",
            conflict=False,
        )
        self.assertEqual(attr.referred_gg_player_id, "5323-5255")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].chat_id, -1001)
        self.assertEqual(
            msgs[0].text,
            "Your referred player's id was 8190-5287 and now is 5323-5255.",
        )
        self.assertNotIn("joined the club", msgs[0].text)

    @patch.object(ref, "fetch_support_group_chat_by_telegram_chat_id")
    @patch.object(ref, "get_db")
    def test_referrer_retitle_pings_referred(self, mock_get_db, mock_sgc):
        mock_sgc.return_value = SimpleNamespace(player_telegram_user_id=999)
        link = _link(
            referrer_chat_id=-1001, referrer_gg_player_id="1111-2222"
        )
        credited = _attr(
            status=ref.STATUS_CREDITED,
            referred_chat_id=-2002,
            referred_gg_player_id="8190-5287",
        )

        session = MagicMock()
        link_q = MagicMock()
        link_q.filter.return_value.one_or_none.return_value = link
        attr_q = MagicMock()
        # First .filter(...).all() for credited kids; then .filter(...).one_or_none()
        attr_filter = MagicMock()
        attr_filter.all.return_value = [credited]
        attr_filter.one_or_none.return_value = None
        attr_q.filter.return_value = attr_filter

        def query_side_effect(model):
            if model is ReferralLink:
                return link_q
            return attr_q

        session.query.side_effect = query_side_effect
        mock_get_db.return_value.__enter__.return_value = session
        mock_get_db.return_value.__exit__.return_value = False

        msgs = ref.on_player_id_bound(
            chat_id=-1001,
            club_id=4,
            gg_player_id="2342-4223",
            previous_gg_player_id="1111-2222",
            conflict=False,
        )
        self.assertEqual(link.referrer_gg_player_id, "2342-4223")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].chat_id, -2002)
        self.assertEqual(
            msgs[0].text,
            "Referred by was 1111-2222 and now is 2342-4223.",
        )

    @patch.object(ref, "fetch_support_group_chat_by_telegram_chat_id")
    @patch.object(ref, "get_db")
    def test_duplicate_conflict_closes_pending(self, mock_get_db, mock_sgc):
        mock_sgc.return_value = SimpleNamespace(player_telegram_user_id=555)
        attr = _attr(status=ref.STATUS_PENDING)
        session = MagicMock()
        session.query.return_value.filter.return_value.one_or_none.return_value = attr
        mock_get_db.return_value.__enter__.return_value = session
        mock_get_db.return_value.__exit__.return_value = False

        msgs = ref.on_player_id_bound(
            chat_id=-2002,
            club_id=4,
            gg_player_id="8190-5287",
            previous_gg_player_id=None,
            conflict=True,
        )
        self.assertEqual(msgs, [])
        self.assertEqual(attr.status, ref.STATUS_CLOSED_DUPLICATE)


class ReferralLinkHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_silent_in_private(self):
        update = SimpleNamespace(
            message=SimpleNamespace(reply_text=AsyncMock()),
            effective_chat=SimpleNamespace(id=123, type="private", title=None),
        )
        context = SimpleNamespace(bot=SimpleNamespace(username="bot"))
        await referral_handler.referral_link_handler(update, context)
        update.message.reply_text.assert_not_called()

    @patch.object(
        referral_handler,
        "fetch_support_group_chat_by_telegram_chat_id",
        return_value=SimpleNamespace(telegram_chat_title="GTO | new"),
    )
    @patch.object(referral_handler, "get_club_for_chat", return_value=4)
    @patch.object(referral_handler, "gg_player_id_from_title", return_value=None)
    async def test_untitled_support_group_errors(self, _title, _club, _sgc):
        update = SimpleNamespace(
            message=SimpleNamespace(reply_text=AsyncMock()),
            effective_chat=SimpleNamespace(
                id=-1001, type="supergroup", title="GTO | new"
            ),
        )
        context = SimpleNamespace(bot=SimpleNamespace(username="bot"))
        await referral_handler.referral_link_handler(update, context)
        update.message.reply_text.assert_awaited_once_with(ref.UNTITLED_GROUP_ERROR)

    @patch.object(
        referral_handler,
        "fetch_support_group_chat_by_telegram_chat_id",
        return_value=None,
    )
    async def test_silent_when_not_support_group(self, _sgc):
        update = SimpleNamespace(
            message=SimpleNamespace(reply_text=AsyncMock()),
            effective_chat=SimpleNamespace(
                id=-999, type="supergroup", title="Random"
            ),
        )
        context = SimpleNamespace(bot=SimpleNamespace(username="bot"))
        await referral_handler.referral_link_handler(update, context)
        update.message.reply_text.assert_not_called()


class MyReferralsHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_silent_in_private(self):
        update = SimpleNamespace(
            message=SimpleNamespace(reply_text=AsyncMock()),
            effective_chat=SimpleNamespace(id=123, type="private", title=None),
        )
        context = SimpleNamespace()
        await referral_handler.my_referrals_handler(update, context)
        update.message.reply_text.assert_not_called()

    @patch.object(
        referral_handler,
        "fetch_support_group_chat_by_telegram_chat_id",
        return_value=None,
    )
    async def test_silent_when_not_support_group(self, _sgc):
        update = SimpleNamespace(
            message=SimpleNamespace(reply_text=AsyncMock()),
            effective_chat=SimpleNamespace(
                id=-999, type="supergroup", title="GTO / 1111-2222 / Player"
            ),
        )
        context = SimpleNamespace()
        await referral_handler.my_referrals_handler(update, context)
        update.message.reply_text.assert_not_called()

    @patch.object(
        referral_handler,
        "fetch_support_group_chat_by_telegram_chat_id",
        return_value=SimpleNamespace(telegram_chat_title="GTO | new"),
    )
    @patch.object(referral_handler, "get_club_for_chat", return_value=4)
    @patch.object(referral_handler, "gg_player_id_from_title", return_value=None)
    async def test_untitled_support_group_errors(self, _title, _club, _sgc):
        update = SimpleNamespace(
            message=SimpleNamespace(reply_text=AsyncMock()),
            effective_chat=SimpleNamespace(
                id=-1001, type="supergroup", title="GTO | new"
            ),
        )
        context = SimpleNamespace()
        await referral_handler.my_referrals_handler(update, context)
        update.message.reply_text.assert_awaited_once_with(ref.UNTITLED_GROUP_ERROR)

    @patch.object(
        referral_handler,
        "fetch_support_group_chat_by_telegram_chat_id",
        return_value=SimpleNamespace(
            telegram_chat_title="GTO / 1111-2222 / Player"
        ),
    )
    @patch.object(referral_handler, "get_club_for_chat", return_value=4)
    @patch.object(
        referral_handler, "gg_player_id_from_title", return_value="1111-2222"
    )
    @patch.object(
        referral_handler,
        "get_credited_referral_player_ids",
        return_value=["5323-5255", "8190-5287"],
    )
    async def test_lists_credited_referrals(
        self, mock_get_ids, _title, _club, _sgc
    ):
        update = SimpleNamespace(
            message=SimpleNamespace(reply_text=AsyncMock()),
            effective_chat=SimpleNamespace(
                id=-1001,
                type="supergroup",
                title="GTO / 1111-2222 / Player",
            ),
        )
        context = SimpleNamespace()
        await referral_handler.my_referrals_handler(update, context)

        mock_get_ids.assert_called_once_with(club_id=4, referrer_chat_id=-1001)
        update.message.reply_text.assert_awaited_once_with(
            "You have referred 2 players:\n\n• 5323-5255\n• 8190-5287"
        )

    @patch.object(
        referral_handler,
        "fetch_support_group_chat_by_telegram_chat_id",
        return_value=SimpleNamespace(
            telegram_chat_title="GTO / 1111-2222 / Player"
        ),
    )
    @patch.object(referral_handler, "get_club_for_chat", return_value=4)
    @patch.object(
        referral_handler, "gg_player_id_from_title", return_value="1111-2222"
    )
    @patch.object(
        referral_handler, "get_credited_referral_player_ids", return_value=[]
    )
    async def test_empty_state(self, _get_ids, _title, _club, _sgc):
        update = SimpleNamespace(
            message=SimpleNamespace(reply_text=AsyncMock()),
            effective_chat=SimpleNamespace(
                id=-1001,
                type="supergroup",
                title="GTO / 1111-2222 / Player",
            ),
        )
        context = SimpleNamespace()
        await referral_handler.my_referrals_handler(update, context)
        update.message.reply_text.assert_awaited_once_with(
            "You haven't referred any players yet."
        )


if __name__ == "__main__":
    unittest.main()
