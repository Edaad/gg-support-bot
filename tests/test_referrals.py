"""Tests for referral deep links (track + ack)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers import referral as referral_handler
from bot.services import referrals as ref
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


if __name__ == "__main__":
    unittest.main()
