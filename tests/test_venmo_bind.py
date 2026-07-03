"""Unit tests for Venmo payment binding and notifications."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import venmo_payments as vp
from db.models import VenmoPayerBinding, VenmoPayment

CHAT_ID = -1001234567890
CLUB_ID = 2
GROUP_TITLE = "RT / 6485-8168 / Angus Mcgoon"
NOTIF_CHAT_ID = -1009999999999
NOTIF_MSG_ID = 12345


class VenmoPaymentsHelpersTestCase(unittest.TestCase):
    def test_normalize_payer_name(self):
        self.assertEqual(vp.normalize_payer_name("  Moshe   Toussoun "), "moshe toussoun")

    def test_normalize_venmo_handle(self):
        self.assertEqual(vp.normalize_venmo_handle("godfather4444"), "@godfather4444")
        self.assertEqual(vp.normalize_venmo_handle("@Godfather4444"), "@godfather4444")

    def test_parse_amount_cents(self):
        self.assertEqual(vp.parse_amount_cents("200.00"), 20000)
        self.assertEqual(vp.parse_amount_cents("$1,234.56"), 123456)

    def test_format_notification_unbound(self):
        payment = VenmoPayment(
            payer_name="Moshe Toussoun",
            amount_cents=20000,
            venmo_handle="@godfather4444",
            goods_or_services=False,
        )
        text = vp.format_notification_text(payment)
        self.assertIn("Unbound", text)
        self.assertIn("Moshe Toussoun", text)
        self.assertIn("Amount: <b>$200</b>", text)
        self.assertLess(text.index("Group Chat:"), text.index("Name:"))
        self.assertNotIn("Open group chat", text)

    def test_format_notification_bound_includes_linked_chat_link(self):
        payment = VenmoPayment(
            payer_name="Moshe Toussoun",
            amount_cents=20000,
            venmo_handle="@godfather4444",
            goods_or_services=False,
            telegram_chat_id=CHAT_ID,
        )
        text = vp.format_notification_text(
            payment,
            group_title=GROUP_TITLE,
        )
        self.assertIn(GROUP_TITLE, text)
        self.assertIn("Player ID: <code>6485-8168</code>", text)
        self.assertIn('<a href="https://t.me/c/1234567890">', text)
        self.assertNotIn("Open group chat", text)
        self.assertLess(text.index("Group Chat:"), text.index("Name:"))

    def test_format_amount_display_rounds_to_whole_dollars(self):
        self.assertEqual(vp.format_amount_display(500), "$5")
        self.assertEqual(vp.format_amount_display(8999), "$90")
        self.assertEqual(vp.format_amount_display(8950), "$90")
        self.assertEqual(vp.format_amount_display(20000), "$200")
        self.assertEqual(vp.format_amount_display(20000, bold=True), "<b>$200</b>")

    def test_format_notification_test_banner(self):
        payment = VenmoPayment(
            payer_name="Moshe Toussoun",
            amount_cents=20000,
            venmo_handle="@godfather4444",
            goods_or_services=False,
            is_test=True,
        )
        text = vp.format_notification_text(payment)
        self.assertTrue(text.startswith("TEST (Please ignore)\n\n"))
        self.assertIn("Venmo Payment Notification", text)

    def test_format_notification_bound(self):
        payment = VenmoPayment(
            payer_name="Moshe Toussoun",
            amount_cents=20000,
            venmo_handle="@godfather4444",
            goods_or_services=False,
        )
        text = vp.format_notification_text(
            payment,
            group_title=GROUP_TITLE,
        )
        self.assertIn(GROUP_TITLE, text)
        self.assertIn("Player ID: <code>6485-8168</code>", text)
        self.assertIn("Method: @godfather4444", text)
        self.assertNotIn("auto-bound", text)
        self.assertNotIn("rebind", text)
        self.assertLess(text.index("Group Chat:"), text.index("Name:"))

    def test_format_notification_includes_memo(self):
        payment = VenmoPayment(
            payer_name="Daniel Cushing",
            amount_cents=20000,
            venmo_handle="@danielcushing",
            goods_or_services=False,
            memo="🍕",
        )
        text = vp.format_notification_text(payment)
        self.assertIn("Memo: 🍕", text)
        self.assertLess(text.index("Amount:"), text.index("Memo:"))
        self.assertLess(text.index("Memo:"), text.index("Method:"))

    def test_format_notification_goods_services_shows_do_not_add(self):
        payment = VenmoPayment(
            payer_name="Jackson Taylor",
            amount_cents=8000,
            venmo_handle="@godfather4444",
            goods_or_services=True,
        )
        text = vp.format_notification_text(payment)
        self.assertIn("DO NOT ADD", text)
        self.assertIn("Goods/Services: True", text)

    def test_format_notification_non_goods_services_no_do_not_add(self):
        payment = VenmoPayment(
            payer_name="Harry Chen",
            amount_cents=10000,
            venmo_handle="@jagger4444",
            goods_or_services=False,
        )
        text = vp.format_notification_text(payment)
        self.assertNotIn("DO NOT ADD", text)
        self.assertIn("Goods/Services: False", text)

    def test_format_setup_already_linked_warning(self):
        from datetime import datetime, timezone

        payment = VenmoPayment(
            payer_name="Vito Corleone",
            amount_cents=500,
            venmo_handle="@michaelc4444",
            goods_or_services=False,
            memo="FLOP",
        )
        last_at = datetime(2026, 6, 4, 23, 27, tzinfo=timezone.utc)
        text = vp.format_setup_already_linked_warning(
            payment,
            already_bound_group_title=GROUP_TITLE,
            last_deposit_at=last_at,
            setup_chat_title="RT / 9999-0000 / New Setup",
        )
        self.assertIn("First-time setup warning", text)
        self.assertIn(GROUP_TITLE, text)
        self.assertIn("Auto-bind attempt from RT / 9999-0000 / New Setup blocked.", text)
        self.assertIn("Player ID: <code>6485-8168</code>", text)
        self.assertIn("Last deposit: Jun 04, 2026 07:27 PM EST", text)
        self.assertIn("left unbound for manual review", text)
        self.assertIn("Memo: FLOP", text)
        self.assertIn("Setup chat: RT / 9999-0000 / New Setup", text)
        self.assertNotIn("Open group chat", text)

    def test_format_setup_already_linked_warning_multi_candidate(self):
        from datetime import datetime, timezone

        payment = VenmoPayment(
            payer_name="Vito Corleone",
            amount_cents=500,
            venmo_handle="@michaelc4444",
            goods_or_services=False,
        )
        other_title = "RT / 1111-2222 / Other Player"
        text = vp.format_setup_already_linked_warning(
            payment,
            already_bound_group_title=GROUP_TITLE,
            already_bound_group_titles=[GROUP_TITLE, other_title],
            last_deposit_at=datetime(2026, 6, 4, 23, 27, tzinfo=timezone.utc),
            setup_chat_title="RT / 9999-0000 / New Setup",
        )
        self.assertIn("FIRST-TIME SETUP WARNING — VERIFY BEFORE BINDING", text)
        self.assertIn("MULTIPLE GROUPS", text)
        self.assertIn("FIRST-DEPOSIT BONUS", text)
        self.assertIn(GROUP_TITLE, text)
        self.assertIn(other_title, text)

    def test_format_setup_already_linked_warning_no_prior_deposit(self):
        payment = VenmoPayment(
            payer_name="Vito Corleone",
            amount_cents=500,
            venmo_handle="@michaelc4444",
            goods_or_services=False,
        )
        text = vp.format_setup_already_linked_warning(
            payment,
            already_bound_group_title=GROUP_TITLE,
            last_deposit_at=None,
            setup_chat_title=GROUP_TITLE,
        )
        self.assertIn("No prior bound deposits found", text)


class SetupAlreadyLinkedIngestTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_setup_match_already_linked_leaves_unbound(self):
        from datetime import datetime, timezone

        from bot.services.payment_method_binding import ExistingVenmoLink

        attempt = MagicMock()
        attempt.id = 12
        attempt.telegram_chat_id = -1002000
        attempt.club_id = CLUB_ID
        attempt.variant_id = 3

        payment_obj = VenmoPayment(
            id=101,
            payer_name="Moshe Toussoun",
            amount_cents=500,
            venmo_handle="@godfather4444",
            goods_or_services=False,
            memo="FLOP",
        )

        def _query(model):
            q = MagicMock()
            if model is VenmoPayment:
                q.filter_by.return_value.one_or_none.side_effect = [None, payment_obj]
            return q

        mock_session = MagicMock()
        mock_session.query.side_effect = _query

        def _add(obj):
            if isinstance(obj, VenmoPayment) and obj.id is None:
                obj.id = 101

        mock_session.add.side_effect = _add
        mock_session.flush = MagicMock()

        send_mock = AsyncMock(return_value=(NOTIF_CHAT_ID, NOTIF_MSG_ID))

        with (
            patch("bot.services.venmo_payments.get_db") as mock_get_db,
            patch(
                "bot.services.venmo_payments.send_telegram_notification",
                new=send_mock,
            ),
            patch(
                "bot.services.venmo_payments.match_pending_memo_setup_in_session",
                return_value=attempt,
            ),
            patch(
                "bot.services.venmo_payments.match_pending_venmo_setup_in_session",
                return_value=None,
            ),
            patch(
                "bot.services.venmo_payments.resolve_display_group_title",
                side_effect=lambda cid: (
                    GROUP_TITLE
                    if cid == CHAT_ID
                    else "RT / 9999-0000 / New Setup"
                ),
            ),
            patch(
                "bot.services.venmo_payments.find_existing_venmo_link_for_setup",
                return_value=ExistingVenmoLink(
                    linked_chat_ids=(CHAT_ID,),
                    via="payer_binding",
                ),
            ),
            patch(
                "bot.services.venmo_payments.get_last_bound_deposit_at",
                return_value=datetime(2026, 6, 4, 23, 27, tzinfo=timezone.utc),
            ),
            patch("bot.services.venmo_payments.track_ingest_notification"),
            patch(
                "bot.services.payment_bind_candidates.candidate_chat_ids",
                return_value=[CHAT_ID],
            ),
            patch(
                "bot.services.venmo_payments.cancel_setup_attempt_in_session",
                return_value=True,
            ) as cancel_mock,
            patch(
                "bot.services.venmo_payments.complete_attempt_in_session",
            ) as complete_mock,
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            result = await vp.ingest_venmo_payment(
                payer_name="Moshe Toussoun",
                amount="5.00",
                venmo_handle="@godfather4444",
                memo="FLOP",
            )

        self.assertFalse(result.auto_bound)
        self.assertEqual(result.status, "unbound")
        complete_mock.assert_not_called()
        cancel_mock.assert_called_once()
        self.assertEqual(send_mock.await_count, 2)
        warning_text = send_mock.await_args_list[0].args[0]
        self.assertIn("First-time setup warning", warning_text)
        self.assertIn(GROUP_TITLE, warning_text)
        payment_text = send_mock.await_args_list[1].args[0]
        self.assertIn("Unbound", payment_text)


class ResolveBoundGroupTestCase(unittest.TestCase):
    @patch("bot.services.venmo_payments.find_group_chat_id_by_name", return_value=CHAT_ID)
    @patch("bot.services.venmo_payments.resolve_club_id_from_shorthand", return_value=CLUB_ID)
    @patch("bot.services.venmo_payments.parse_tracking_title", return_value=("RT", "6485-8168"))
    @patch(
        "bot.services.venmo_payments.resolve_display_group_title",
        return_value=GROUP_TITLE,
    )
    def test_resolve_bound_group_success(self, *_mocks):
        result = vp.resolve_bound_group(GROUP_TITLE)
        self.assertTrue(result.ok)
        assert result.bound_group is not None
        self.assertEqual(result.bound_group.telegram_chat_id, CHAT_ID)
        self.assertEqual(result.bound_group.group_title, GROUP_TITLE)

    @patch("bot.services.venmo_payments.find_group_chat_id_by_name", return_value=None)
    @patch("bot.services.venmo_payments.resolve_club_id_from_shorthand", return_value=CLUB_ID)
    @patch("bot.services.venmo_payments.parse_tracking_title", return_value=("RT", "6485-8168"))
    def test_resolve_bound_group_not_found(self, *_mocks):
        result = vp.resolve_bound_group(GROUP_TITLE)
        self.assertFalse(result.ok)
        self.assertIn("No linked group", result.error or "")


class VenmoBindFlowTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_bind_updates_payment(self):
        payment = VenmoPayment(
            id=1,
            payer_name="Moshe Toussoun",
            amount_cents=20000,
            venmo_handle="@godfather4444",
            goods_or_services=False,
            notification_chat_id=NOTIF_CHAT_ID,
            notification_message_id=NOTIF_MSG_ID,
        )
        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_query.filter_by.return_value.one_or_none.return_value = payment
        mock_session.query.return_value = mock_query

        with (
            patch("bot.services.venmo_payments.get_db") as mock_get_db,
            patch(
                "bot.services.venmo_payments.edit_telegram_notification",
                new=AsyncMock(),
            ),
            patch(
                "bot.services.venmo_payments.resolve_bound_group",
                return_value=vp.BindResult(
                    ok=True,
                    bound_group=vp.BoundGroup(
                        telegram_chat_id=CHAT_ID,
                        club_id=CLUB_ID,
                        group_title=GROUP_TITLE,
                    ),
                ),
            ),
            patch(
                "bot.services.venmo_payments.resolve_display_group_title",
                return_value=GROUP_TITLE,
            ),
            patch(
                "bot.services.venmo_payments.infer_variant_id_for_venmo_handle",
                return_value=None,
            ),
            patch("bot.services.venmo_payments.record_group_binding_in_session"),
            patch("bot.services.venmo_payments.record_payment_bound"),
            patch(
                "bot.services.venmo_payments.sync_payment_notification_edit",
                new=AsyncMock(),
            ),
            patch(
                "bot.services.venmo_payments.maybe_notify_player_on_auto_bound",
                new=AsyncMock(),
            ) as player_notify_mock,
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            result = await vp.bind_venmo_payment_from_reply(
                notification_chat_id=NOTIF_CHAT_ID,
                notification_message_id=NOTIF_MSG_ID,
                group_title_input=GROUP_TITLE,
                bound_by_telegram_user_id=493310710,
            )

        self.assertTrue(result.ok)
        self.assertEqual(payment.telegram_chat_id, CHAT_ID)
        self.assertEqual(payment.club_id, CLUB_ID)
        self.assertFalse(payment.auto_bound)
        self.assertIsNotNone(payment.bound_at)
        player_notify_mock.assert_not_awaited()

    async def test_ingest_auto_binds_known_payer(self):
        from bot.services.payment_bind_candidates import CandidateGroup

        payment_obj = VenmoPayment(
            id=99,
            payer_name="Moshe Toussoun",
            amount_cents=20000,
            venmo_handle="@godfather4444",
            goods_or_services=False,
            telegram_chat_id=CHAT_ID,
            club_id=CLUB_ID,
        )

        def _query(model):
            q = MagicMock()
            if model is VenmoPayment:
                q.filter_by.return_value.one_or_none.side_effect = [None, payment_obj]
                q.filter_by.return_value.one.return_value = payment_obj
            return q

        mock_session = MagicMock()
        mock_session.query.side_effect = _query

        def _add(obj):
            if isinstance(obj, VenmoPayment) and obj.id is None:
                obj.id = 99

        mock_session.add.side_effect = _add
        mock_session.flush = MagicMock()

        single = CandidateGroup(
            telegram_chat_id=CHAT_ID,
            club_id=CLUB_ID,
            group_title=GROUP_TITLE,
        )

        with (
            patch("bot.services.venmo_payments.get_db") as mock_get_db,
            patch(
                "bot.services.venmo_payments.send_telegram_notification",
                new=AsyncMock(return_value=(NOTIF_CHAT_ID, NOTIF_MSG_ID)),
            ),
            patch(
                "bot.services.venmo_payments.resolve_display_group_title",
                return_value=GROUP_TITLE,
            ),
            patch(
                "bot.services.payment_bind_candidates.candidates_for_payment",
                return_value=[single],
            ),
            patch("bot.services.venmo_payments.track_ingest_notification"),
            patch(
                "bot.services.venmo_payments.maybe_notify_player_on_auto_bound",
                new=AsyncMock(),
            ) as player_notify_mock,
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            result = await vp.ingest_venmo_payment(
                payer_name="Moshe Toussoun",
                amount="200.00",
                venmo_handle="@godfather4444",
            )

        self.assertTrue(result.auto_bound)
        self.assertEqual(result.status, "bound")
        player_notify_mock.assert_awaited_once_with(
            telegram_chat_id=CHAT_ID,
            amount_cents=20000,
            auto_bound=True,
            is_test=False,
            goods_or_services=False,
        )

    async def test_ingest_auto_binds_known_payer_different_recipient_handle(self):
        from bot.services.payment_bind_candidates import CandidateGroup

        payment_obj = VenmoPayment(
            id=100,
            payer_name="Moshe Toussoun",
            amount_cents=15000,
            venmo_handle="@other-venmo",
            goods_or_services=False,
            telegram_chat_id=CHAT_ID,
            club_id=CLUB_ID,
        )

        def _query(model):
            q = MagicMock()
            if model is VenmoPayment:
                q.filter_by.return_value.one_or_none.side_effect = [None, payment_obj]
                q.filter_by.return_value.one.return_value = payment_obj
            return q

        mock_session = MagicMock()
        mock_session.query.side_effect = _query

        def _add(obj):
            if isinstance(obj, VenmoPayment) and obj.id is None:
                obj.id = 100

        mock_session.add.side_effect = _add
        mock_session.flush = MagicMock()

        single = CandidateGroup(
            telegram_chat_id=CHAT_ID,
            club_id=CLUB_ID,
            group_title=GROUP_TITLE,
        )

        with (
            patch("bot.services.venmo_payments.get_db") as mock_get_db,
            patch(
                "bot.services.venmo_payments.send_telegram_notification",
                new=AsyncMock(return_value=(NOTIF_CHAT_ID, NOTIF_MSG_ID)),
            ),
            patch(
                "bot.services.venmo_payments.resolve_display_group_title",
                return_value=GROUP_TITLE,
            ),
            patch(
                "bot.services.payment_bind_candidates.candidates_for_payment",
                return_value=[single],
            ),
            patch("bot.services.venmo_payments.track_ingest_notification"),
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            result = await vp.ingest_venmo_payment(
                payer_name="Moshe Toussoun",
                amount="150.00",
                venmo_handle="@other-venmo",
            )

        self.assertTrue(result.auto_bound)
        self.assertEqual(result.status, "bound")

    async def test_ingest_test_ambiguous_two_test_candidates(self):
        from bot.services.payment_bind_candidates import CandidateGroup
        from notification.formatting import AMBIGUOUS_GROUP_CHAT_LINE

        payment_obj = VenmoPayment(
            id=110,
            payer_name="Winson Dong",
            amount_cents=20000,
            venmo_handle="@godfather4444",
            goods_or_services=False,
            is_test=True,
        )

        def _query(model):
            q = MagicMock()
            if model is VenmoPayment:
                q.filter_by.return_value.one_or_none.side_effect = [None, payment_obj]
                q.filter_by.return_value.one.return_value = payment_obj
            return q

        mock_session = MagicMock()
        mock_session.query.side_effect = _query

        def _add(obj):
            if isinstance(obj, VenmoPayment) and obj.id is None:
                obj.id = 110

        mock_session.add.side_effect = _add
        mock_session.flush = MagicMock()

        test_candidates = [
            CandidateGroup(
                telegram_chat_id=-1001,
                club_id=CLUB_ID,
                group_title="CC / 4334-4433 / TEST",
            ),
            CandidateGroup(
                telegram_chat_id=-1002,
                club_id=CLUB_ID,
                group_title="CC / 5555-5555 / TEST",
            ),
        ]

        send_mock = AsyncMock(return_value=(NOTIF_CHAT_ID, NOTIF_MSG_ID))

        with (
            patch("bot.services.venmo_payments.get_db") as mock_get_db,
            patch(
                "bot.services.venmo_payments.send_telegram_notification",
                new=send_mock,
            ),
            patch(
                "bot.services.venmo_payments.match_pending_memo_setup_in_session",
                return_value=None,
            ),
            patch(
                "bot.services.venmo_payments.match_pending_venmo_setup_in_session",
                return_value=None,
            ),
            patch(
                "bot.services.payment_bind_candidates.list_candidate_groups",
                return_value=test_candidates,
            ),
            patch("bot.services.venmo_payments.track_ingest_notification"),
            patch(
                "bot.services.venmo_payments.maybe_notify_player_on_auto_bound",
                new=AsyncMock(),
            ) as player_notify_mock,
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            result = await vp.ingest_venmo_payment(
                payer_name="Winson Dong",
                amount="200.00",
                venmo_handle="@godfather4444",
                test=True,
            )

        self.assertFalse(result.auto_bound)
        self.assertEqual(result.status, "unbound")
        player_notify_mock.assert_awaited_once_with(
            telegram_chat_id=None,
            amount_cents=20000,
            auto_bound=False,
            is_test=True,
            goods_or_services=False,
        )
        payment_text = send_mock.await_args.kwargs.get("text") or send_mock.await_args.args[0]
        self.assertIn(AMBIGUOUS_GROUP_CHAT_LINE, payment_text)
        markup = send_mock.await_args.kwargs.get("reply_markup")
        self.assertIsNotNone(markup)
        self.assertGreater(len(markup.get("inline_keyboard") or []), 0)

    async def test_ingest_test_auto_binds_single_test_candidate(self):
        from bot.services.payment_bind_candidates import CandidateGroup

        payment_obj = VenmoPayment(
            id=111,
            payer_name="Winson Dong",
            amount_cents=20000,
            venmo_handle="@godfather4444",
            goods_or_services=False,
            is_test=True,
            telegram_chat_id=-1001,
            club_id=CLUB_ID,
        )

        def _query(model):
            q = MagicMock()
            if model is VenmoPayment:
                q.filter_by.return_value.one_or_none.side_effect = [None, payment_obj]
                q.filter_by.return_value.one.return_value = payment_obj
            return q

        mock_session = MagicMock()
        mock_session.query.side_effect = _query

        def _add(obj):
            if isinstance(obj, VenmoPayment) and obj.id is None:
                obj.id = 111

        mock_session.add.side_effect = _add
        mock_session.flush = MagicMock()

        single_test = CandidateGroup(
            telegram_chat_id=-1001,
            club_id=CLUB_ID,
            group_title="CC / 4334-4433 / TEST",
        )

        with (
            patch("bot.services.venmo_payments.get_db") as mock_get_db,
            patch(
                "bot.services.venmo_payments.send_telegram_notification",
                new=AsyncMock(return_value=(NOTIF_CHAT_ID, NOTIF_MSG_ID)),
            ),
            patch(
                "bot.services.venmo_payments.resolve_display_group_title",
                return_value="CC / 4334-4433 / TEST",
            ),
            patch(
                "bot.services.payment_bind_candidates.candidates_for_payment",
                return_value=[single_test],
            ),
            patch("bot.services.venmo_payments.track_ingest_notification"),
            patch(
                "bot.services.venmo_payments.maybe_notify_player_on_auto_bound",
                new=AsyncMock(),
            ) as player_notify_mock,
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            result = await vp.ingest_venmo_payment(
                payer_name="Winson Dong",
                amount="200.00",
                venmo_handle="@godfather4444",
                test=True,
            )

        self.assertTrue(result.auto_bound)
        self.assertEqual(result.status, "bound")
        player_notify_mock.assert_awaited_once_with(
            telegram_chat_id=-1001,
            amount_cents=20000,
            auto_bound=True,
            is_test=True,
            goods_or_services=False,
        )

    async def test_ingest_idempotent_reject_logs_and_skips_telegram(self):
        existing = VenmoPayment(
            id=5,
            payer_name="Aravindh Soundararajan",
            amount_cents=30000,
            venmo_handle="@godfather4444",
            goods_or_services=False,
            source_external_id="1974050a62bcb581",
            telegram_chat_id=CHAT_ID,
        )

        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_query.filter_by.return_value.one_or_none.return_value = existing
        mock_session.query.return_value = mock_query

        with (
            patch("bot.services.venmo_payments.get_db") as mock_get_db,
            patch(
                "bot.services.venmo_payments.send_telegram_notification",
                new=AsyncMock(),
            ) as mock_send,
            self.assertLogs("bot.services.venmo_payments", level="INFO") as logs,
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            result = await vp.ingest_venmo_payment(
                payer_name="Jungwook Youn",
                amount="300.00",
                venmo_handle="@godfather4444",
                source_external_id="1974050a62bcb581",
            )

        self.assertFalse(result.created)
        self.assertEqual(result.payment_id, 5)
        mock_send.assert_not_called()
        self.assertTrue(
            any("idempotent reject" in msg for msg in logs.output),
            logs.output,
        )
        self.assertTrue(
            any("Jungwook Youn" in msg and "Aravindh Soundararajan" in msg for msg in logs.output),
            logs.output,
        )


class LiveTitleAfterRenameTestCase(unittest.TestCase):
    @patch(
        "bot.services.venmo_payments.get_group_title_for_chat",
        return_value=("RT / 6485-8168 / Angus M", CLUB_ID),
    )
    def test_resolve_display_group_title_uses_live_name(self, _mock):
        title = vp.resolve_display_group_title(CHAT_ID)
        self.assertEqual(title, "RT / 6485-8168 / Angus M")


if __name__ == "__main__":
    unittest.main()
