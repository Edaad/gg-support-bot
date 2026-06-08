"""Tests for crypto alert scope mapping and bind validation."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.crypto_payments import (
    ALERT_NAME_CLUBGTO,
    ALERT_NAME_RT_AT_CC,
    ALERT_SCOPE_CLUBGTO,
    ALERT_SCOPE_RT_AT_CC,
    alert_scope_for_club_name,
    ingest_crypto_payment,
    resolve_alert_scope,
    validate_bind_alert_scope,
)
from bot.services.venmo_payments import BindResult
from db.models import CryptoPayment


class CryptoAlertScopeTestCase(unittest.TestCase):
    def test_resolve_alert_scope_clubgto(self):
        self.assertEqual(resolve_alert_scope(ALERT_NAME_CLUBGTO), ALERT_SCOPE_CLUBGTO)

    def test_resolve_alert_scope_rt_at_cc(self):
        self.assertEqual(resolve_alert_scope(ALERT_NAME_RT_AT_CC), ALERT_SCOPE_RT_AT_CC)

    def test_resolve_alert_scope_case_insensitive(self):
        self.assertEqual(
            resolve_alert_scope("clubgto crypto payment"),
            ALERT_SCOPE_CLUBGTO,
        )

    def test_resolve_alert_scope_unknown_rejected(self):
        with self.assertRaises(ValueError):
            resolve_alert_scope("Other Crypto Payment")

    def test_club_name_to_alert_scope(self):
        self.assertEqual(alert_scope_for_club_name("ClubGTO"), ALERT_SCOPE_CLUBGTO)
        self.assertEqual(alert_scope_for_club_name("Round Table"), ALERT_SCOPE_RT_AT_CC)
        self.assertEqual(alert_scope_for_club_name("Creator Club"), ALERT_SCOPE_RT_AT_CC)
        self.assertIsNone(alert_scope_for_club_name("Some Other Club"))

    def test_validate_bind_scope_clubgto_mismatch(self):
        payment = MagicMock()
        payment.alert_scope = ALERT_SCOPE_CLUBGTO
        with patch("bot.services.crypto_payments.get_db") as mock_get_db:
            session = MagicMock()
            mock_get_db.return_value.__enter__.return_value = session
            club = MagicMock()
            club.name = "Round Table"
            session.query.return_value.filter_by.return_value.one_or_none.return_value = club
            result = validate_bind_alert_scope(payment, bound_club_id=2)
        self.assertIsInstance(result, BindResult)
        assert result is not None
        self.assertFalse(result.ok)
        self.assertIn("ClubGTO", result.error or "")

    def test_validate_bind_scope_match(self):
        payment = MagicMock()
        payment.alert_scope = ALERT_SCOPE_RT_AT_CC
        with patch("bot.services.crypto_payments.get_db") as mock_get_db:
            session = MagicMock()
            mock_get_db.return_value.__enter__.return_value = session
            club = MagicMock()
            club.name = "Creator Club"
            session.query.return_value.filter_by.return_value.one_or_none.return_value = club
            result = validate_bind_alert_scope(payment, bound_club_id=3)
        self.assertIsNone(result)


class CryptoIngestIdempotencyTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_source_external_id_skips_second_notification(self):
        existing = CryptoPayment(
            id=5,
            amount_cents=2500,
            token_symbol="USDT",
            chain="bsc",
            from_address="0xabc",
            to_address="0xdef",
            transaction_hash="0xtx",
            source_external_id="ext-1",
            alert_name=ALERT_NAME_CLUBGTO,
            alert_scope=ALERT_SCOPE_CLUBGTO,
            notification_message_id=None,
        )
        session = MagicMock()
        query = MagicMock()
        query.filter_by.return_value.one_or_none.return_value = existing
        session.query.return_value = query

        with patch("bot.services.crypto_payments.get_db") as get_db_mock:
            get_db_mock.return_value.__enter__.return_value = session
            send_mock = AsyncMock()
            with patch(
                "bot.services.crypto_payments.send_telegram_notification",
                send_mock,
            ):
                result = await ingest_crypto_payment(
                    amount="25",
                    token_symbol="USDT",
                    chain="bsc",
                    from_address="0xabc",
                    to_address="0xdef",
                    transaction_hash="0xtx",
                    alert_name=ALERT_NAME_CLUBGTO,
                    source_external_id="ext-1",
                )

        self.assertFalse(result.created)
        self.assertEqual(result.payment_id, 5)
        send_mock.assert_not_called()


class FormatPaidAtDisplayTestCase(unittest.TestCase):
    def test_iso_z_suffix(self):
        from bot.services.venmo_payments import format_paid_at_display

        self.assertEqual(
            format_paid_at_display("2026-06-06T21:21:35Z"),
            "Jun 06, 2026 05:21 PM EST",
        )

    def test_unparseable_passthrough(self):
        from bot.services.venmo_payments import format_paid_at_display

        self.assertEqual(format_paid_at_display("not-a-date"), "not-a-date")


if __name__ == "__main__":
    unittest.main()
