"""Tests for Venmo Goods & Services issue report automation."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import venmo_payments as vp
from db.models import VenmoPayment

CHAT_ID = -1001234567890
CLUB_ID = 2
GROUP_TITLE = "RT / 6485-8168 / Angus Mcgoon"
NOTIF_CHAT_ID = -1009999999999
NOTIF_MSG_ID = 12345


class VenmoGoodsServicesIssueReportTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_skips_when_not_goods_or_services(self):
        payment = VenmoPayment(
            id=1,
            payer_name="Alice",
            amount_cents=10000,
            venmo_handle="@alice",
            goods_or_services=False,
        )
        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.one.return_value = payment
        with (
            patch("bot.services.venmo_payments.get_db") as mock_get_db,
            patch(
                "bot.services.issue_reports.create_issue_report",
                new=AsyncMock(),
            ) as create_mock,
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)
            await vp.maybe_create_venmo_goods_services_issue_report(payment)
        create_mock.assert_not_awaited()

    async def test_creates_deposit_report_for_goods_or_services(self):
        payment = VenmoPayment(
            id=42,
            payer_name="Jackson Taylor",
            amount_cents=8000,
            venmo_handle="@godfather4444",
            goods_or_services=True,
            club_id=CLUB_ID,
            telegram_chat_id=CHAT_ID,
            memo="🍕",
        )
        create_mock = AsyncMock()
        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.one.return_value = payment
        with (
            patch("bot.services.venmo_payments.get_db") as mock_get_db,
            patch(
                "bot.services.issue_reports.create_issue_report",
                new=create_mock,
            ),
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)
            await vp.maybe_create_venmo_goods_services_issue_report(
                payment,
                group_title=GROUP_TITLE,
                notification_chat_id=NOTIF_CHAT_ID,
                notification_message_id=NOTIF_MSG_ID,
            )

        create_mock.assert_awaited_once()
        kwargs = create_mock.await_args.kwargs
        self.assertEqual(kwargs["category"], "deposit")
        self.assertEqual(kwargs["notify_tags"], ["head_admin"])
        self.assertEqual(kwargs["reporter_source"], "venmo_ingest")
        self.assertEqual(kwargs["reporter_name"], "GG Support Bot")
        self.assertEqual(kwargs["club_id"], CLUB_ID)
        self.assertEqual(kwargs["group_title"], GROUP_TITLE)
        self.assertEqual(kwargs["telegram_chat_id"], CHAT_ID)
        self.assertIn("Jackson Taylor", kwargs["title"])
        self.assertIn("DO NOT ADD", kwargs["description"])
        self.assertIn("Payment ID: 42", kwargs["description"])
        self.assertIn(f"message_id={NOTIF_MSG_ID}", kwargs["description"])

    async def test_ingest_goods_services_creates_issue_report(self):
        payment_obj = VenmoPayment(
            id=99,
            payer_name="Jackson Taylor",
            amount_cents=8000,
            venmo_handle="@godfather4444",
            goods_or_services=True,
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
                obj.goods_or_services = True

        mock_session.add.side_effect = _add
        mock_session.flush = MagicMock()

        with (
            patch("bot.services.venmo_payments.get_db") as mock_get_db,
            patch(
                "bot.services.venmo_payments.send_telegram_notification",
                new=AsyncMock(return_value=(NOTIF_CHAT_ID, NOTIF_MSG_ID)),
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
                "bot.services.payment_bind_candidates.candidates_for_payment",
                return_value=[],
            ),
            patch("bot.services.venmo_payments.track_ingest_notification"),
            patch(
                "bot.services.venmo_payments.maybe_notify_player_on_auto_bound",
                new=AsyncMock(),
            ),
            patch(
                "bot.services.venmo_payments.maybe_create_venmo_goods_services_issue_report",
                new=AsyncMock(),
            ) as report_mock,
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            result = await vp.ingest_venmo_payment(
                payer_name="Jackson Taylor",
                amount="80.00",
                venmo_handle="@godfather4444",
                goods_or_services=True,
            )

        self.assertTrue(result.created)
        report_mock.assert_awaited_once()

    async def test_idempotent_duplicate_skips_issue_report(self):
        existing = VenmoPayment(
            id=1,
            payer_name="Jackson Taylor",
            amount_cents=8000,
            venmo_handle="@godfather4444",
            goods_or_services=True,
        )
        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.one_or_none.return_value = (
            existing
        )

        with (
            patch("bot.services.venmo_payments.get_db") as mock_get_db,
            patch(
                "bot.services.venmo_payments.maybe_create_venmo_goods_services_issue_report",
                new=AsyncMock(),
            ) as report_mock,
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            result = await vp.ingest_venmo_payment(
                payer_name="Jackson Taylor",
                amount="80.00",
                venmo_handle="@godfather4444",
                goods_or_services=True,
                source_external_id="gmail-msg-123",
            )

        self.assertFalse(result.created)
        report_mock.assert_not_awaited()

    async def test_ingest_returns_after_notification_session_closes(self):
        """Regression: ingest must not read ORM attrs after the update session closes."""
        payment_obj = VenmoPayment(
            id=55,
            payer_name="Brayden solberg",
            amount_cents=25000,
            venmo_handle="@godfather4444",
            goods_or_services=False,
        )

        def _query(model):
            q = MagicMock()
            if model is VenmoPayment:
                q.filter_by.return_value.one_or_none.return_value = None
                q.filter_by.return_value.one.return_value = payment_obj
            return q

        mock_session = MagicMock()
        mock_session.query.side_effect = _query

        def _add(obj):
            if isinstance(obj, VenmoPayment) and obj.id is None:
                obj.id = 55

        mock_session.add.side_effect = _add
        mock_session.flush = MagicMock()

        with (
            patch("bot.services.venmo_payments.get_db") as mock_get_db,
            patch(
                "bot.services.venmo_payments.send_telegram_notification",
                new=AsyncMock(return_value=(NOTIF_CHAT_ID, NOTIF_MSG_ID)),
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
                "bot.services.payment_bind_candidates.candidates_for_payment",
                return_value=[],
            ),
            patch("bot.services.venmo_payments.track_ingest_notification"),
            patch(
                "bot.services.venmo_payments.maybe_notify_player_on_auto_bound",
                new=AsyncMock(),
            ) as notify_mock,
            patch(
                "bot.services.venmo_payments.maybe_create_venmo_goods_services_issue_report",
                new=AsyncMock(),
            ) as report_mock,
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            result = await vp.ingest_venmo_payment(
                payer_name="Brayden solberg",
                amount="250.00",
                venmo_handle="@godfather4444",
                goods_or_services=False,
            )

        self.assertTrue(result.created)
        self.assertEqual(result.payment_id, 55)
        notify_mock.assert_awaited_once_with(
            telegram_chat_id=payment_obj.telegram_chat_id,
            amount_cents=25000,
            auto_bound=False,
            is_test=False,
            goods_or_services=False,
        )
        report_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
