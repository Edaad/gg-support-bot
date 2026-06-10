"""Unit tests for Cash App payment binding and notifications."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import cashapp_payments as cp
from db.models import CashAppPayerBinding, CashAppPayment

CHAT_ID = -1001234567890
CLUB_ID = 2
GROUP_TITLE = "RT / 6485-8168 / Angus Mcgoon"
NOTIF_CHAT_ID = -1009999999999
NOTIF_MSG_ID = 12345


class CashAppPaymentsHelpersTestCase(unittest.TestCase):
    def test_normalize_cashapp_handle(self):
        self.assertEqual(cp.normalize_cashapp_handle("michaelc4444"), "$michaelc4444")
        self.assertEqual(cp.normalize_cashapp_handle("$MichaelC4444"), "$michaelc4444")

    def test_format_notification_unbound(self):
        payment = CashAppPayment(
            payer_name="Jackson Taylor",
            amount_cents=1500,
            cashapp_handle="$michaelc4444",
        )
        text = cp.format_notification_text(payment)
        self.assertIn("Unbound", text)
        self.assertIn("Jackson Taylor", text)
        self.assertIn("Method: Cashapp ($michaelc4444)", text)

    def test_format_notification_includes_memo(self):
        payment = CashAppPayment(
            payer_name="Jackson Taylor",
            amount_cents=1500,
            cashapp_handle="$michaelc4444",
            memo="FLOP",
        )
        text = cp.format_notification_text(payment)
        self.assertIn("Memo: FLOP", text)

    def test_format_notification_bound(self):
        payment = CashAppPayment(
            payer_name="Jackson Taylor",
            amount_cents=1500,
            cashapp_handle="$michaelc4444",
        )
        text = cp.format_notification_text(payment, group_title=GROUP_TITLE)
        self.assertIn(GROUP_TITLE, text)
        self.assertIn("Player ID: <code>6485-8168</code>", text)


class CashAppIngestMemoSetupTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_memo_setup_auto_binds(self):
        attempt = MagicMock()
        attempt.id = 12
        attempt.telegram_chat_id = CHAT_ID
        attempt.club_id = CLUB_ID
        attempt.variant_id = 3

        payment_obj = CashAppPayment(
            id=101,
            payer_name="Jackson Taylor",
            amount_cents=500,
            cashapp_handle="$michaelc4444",
            memo="FLOP",
        )

        def _query(model):
            q = MagicMock()
            if model is CashAppPayment:
                q.filter_by.return_value.one_or_none.side_effect = [None, payment_obj]
            elif model is CashAppPayerBinding:
                q.filter_by.return_value.one_or_none.return_value = None
            return q

        mock_session = MagicMock()
        mock_session.query.side_effect = _query

        def _add(obj):
            if isinstance(obj, CashAppPayment) and obj.id is None:
                obj.id = 101

        mock_session.add.side_effect = _add
        mock_session.flush = MagicMock()

        send_mock = AsyncMock(return_value=(NOTIF_CHAT_ID, NOTIF_MSG_ID))

        with (
            patch("bot.services.cashapp_payments.get_db") as mock_get_db,
            patch(
                "bot.services.cashapp_payments.send_telegram_notification",
                new=send_mock,
            ),
            patch(
                "bot.services.cashapp_payments.match_pending_memo_setup_in_session",
                return_value=attempt,
            ),
            patch(
                "bot.services.cashapp_payments.match_pending_cashapp_setup_in_session",
                return_value=None,
            ),
            patch(
                "bot.services.cashapp_payments.find_existing_cashapp_link_for_setup",
                return_value=None,
            ),
            patch(
                "bot.services.cashapp_payments.complete_attempt_in_session",
                return_value=True,
            ) as complete_mock,
            patch(
                "bot.services.cashapp_payments.resolve_display_group_title",
                return_value=GROUP_TITLE,
            ),
            patch(
                "bot.services.cashapp_payments.record_group_binding_in_session",
            ),
        ):
            mock_get_db.return_value.__enter__.return_value = mock_session
            result = await cp.ingest_cashapp_payment(
                payer_name="Jackson Taylor",
                amount="5.00",
                cashapp_handle="$michaelc4444",
                memo="FLOP",
            )

        self.assertTrue(result.auto_bound)
        self.assertEqual(result.status, "bound")
        complete_mock.assert_called_once()
        self.assertEqual(complete_mock.call_args.kwargs["cashapp_payment_id"], 101)
