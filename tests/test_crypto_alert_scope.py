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
    bind_crypto_payment_by_id,
    ingest_crypto_payment,
    normalize_from_address,
    resolve_alert_scope,
    validate_bind_alert_scope,
)
from bot.services.venmo_payments import BindResult, BoundGroup
from db.models import CryptoPayment, CryptoWalletBinding

CHAT_ID = -1001234567890
CLUB_ID = 2
GROUP_TITLE = "GTO / 8190-5287 / PlayerName"
NOTIF_CHAT_ID = -1009999999999
NOTIF_MSG_ID = 12345
FROM_ADDRESS = "0x8894E0a0c962CB723c1976a4421c95949bE2D4E3"


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

    async def test_new_source_external_id_creates_payment(self):
        """Regression: ext_id present but no row yet must not hit idempotent return."""
        session = MagicMock()
        query = MagicMock()
        query.filter_by.return_value.one_or_none.return_value = None
        session.query.return_value = query

        def _add(obj):
            if isinstance(obj, CryptoPayment) and obj.id is None:
                obj.id = 42

        session.add.side_effect = _add
        session.flush = MagicMock()

        with (
            patch("bot.services.crypto_payments.get_db") as get_db_mock,
            patch(
                "bot.services.crypto_payments.send_telegram_notification",
                new=AsyncMock(return_value=(NOTIF_CHAT_ID, NOTIF_MSG_ID)),
            ) as send_mock,
            patch(
                "bot.services.crypto_payments.resolve_group_chat_url_for_payment",
                new=AsyncMock(return_value=None),
            ),
        ):
            get_db_mock.return_value.__enter__.return_value = session
            get_db_mock.return_value.__exit__.return_value = False

            result = await ingest_crypto_payment(
                amount="1000.00",
                token_symbol="USDT",
                chain="tron",
                from_address="TSuuhfxbM5LsS9oC5rijShNNsupn7VcTa5",
                to_address="TZ9LgB7MQjvSmnPqGY1NYNbMh4fYbCdBtD",
                transaction_hash="0xtx",
                alert_name=ALERT_NAME_CLUBGTO,
                source_external_id="tx_0",
            )

        self.assertTrue(result.created)
        self.assertEqual(result.payment_id, 42)
        send_mock.assert_called_once()


class CryptoWalletBindingTestCase(unittest.TestCase):
    def test_normalize_from_address_lowercases(self):
        self.assertEqual(
            normalize_from_address("0x8894E0a0c962CB723c1976a4421c95949bE2D4E3"),
            "0x8894e0a0c962cb723c1976a4421c95949be2d4e3",
        )


class CryptoIngestAutoBindTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_auto_binds_known_wallet(self):
        binding = CryptoWalletBinding(
            from_address_normalized=normalize_from_address(FROM_ADDRESS),
            alert_scope=ALERT_SCOPE_CLUBGTO,
            telegram_chat_id=CHAT_ID,
            club_id=CLUB_ID,
        )
        payment_obj = CryptoPayment(
            id=99,
            amount_cents=12200,
            token_symbol="USDC",
            chain="bsc",
            from_address=FROM_ADDRESS,
            to_address="0x7063760294b901CF56b34BEB6275A641B5178CDa",
            transaction_hash="0xtx",
            alert_name=ALERT_NAME_CLUBGTO,
            alert_scope=ALERT_SCOPE_CLUBGTO,
        )

        def _query(model):
            q = MagicMock()
            if model is CryptoPayment:
                q.filter_by.return_value.one_or_none.side_effect = [None, payment_obj]
            elif model is CryptoWalletBinding:
                q.filter_by.return_value.one_or_none.return_value = binding
            return q

        mock_session = MagicMock()
        mock_session.query.side_effect = _query

        def _add(obj):
            if isinstance(obj, CryptoPayment) and obj.id is None:
                obj.id = 99

        mock_session.add.side_effect = _add
        mock_session.flush = MagicMock()

        with (
            patch("bot.services.crypto_payments.get_db") as mock_get_db,
            patch(
                "bot.services.crypto_payments.send_telegram_notification",
                new=AsyncMock(return_value=(NOTIF_CHAT_ID, NOTIF_MSG_ID)),
            ),
            patch(
                "bot.services.crypto_payments.resolve_display_group_title",
                return_value=GROUP_TITLE,
            ),
            patch(
                "bot.services.crypto_payments.resolve_group_chat_url_for_payment",
                new=AsyncMock(return_value=None),
            ),
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            result = await ingest_crypto_payment(
                amount="122.00",
                token_symbol="USDC",
                chain="bsc",
                from_address=FROM_ADDRESS,
                to_address="0x7063760294b901CF56b34BEB6275A641B5178CDa",
                transaction_hash="0xtx2",
                alert_name=ALERT_NAME_CLUBGTO,
            )

        self.assertTrue(result.auto_bound)
        self.assertEqual(result.status, "bound")

    async def test_ingest_same_address_different_scope_stays_unbound(self):
        binding = CryptoWalletBinding(
            from_address_normalized=normalize_from_address(FROM_ADDRESS),
            alert_scope=ALERT_SCOPE_CLUBGTO,
            telegram_chat_id=CHAT_ID,
            club_id=CLUB_ID,
        )
        payment_obj = CryptoPayment(
            id=100,
            amount_cents=5000,
            token_symbol="USDC",
            chain="bsc",
            from_address=FROM_ADDRESS,
            to_address="0x7063760294b901CF56b34BEB6275A641B5178CDa",
            transaction_hash="0xtx3",
            alert_name=ALERT_NAME_RT_AT_CC,
            alert_scope=ALERT_SCOPE_RT_AT_CC,
        )

        def _query(model):
            q = MagicMock()
            if model is CryptoPayment:
                q.filter_by.return_value.one_or_none.side_effect = [None, payment_obj]
            elif model is CryptoWalletBinding:
                q.filter_by.return_value.one_or_none.return_value = None
            return q

        mock_session = MagicMock()
        mock_session.query.side_effect = _query

        def _add(obj):
            if isinstance(obj, CryptoPayment) and obj.id is None:
                obj.id = 100

        mock_session.add.side_effect = _add
        mock_session.flush = MagicMock()

        with (
            patch("bot.services.crypto_payments.get_db") as mock_get_db,
            patch(
                "bot.services.crypto_payments.send_telegram_notification",
                new=AsyncMock(return_value=(NOTIF_CHAT_ID, NOTIF_MSG_ID)),
            ),
            patch(
                "bot.services.crypto_payments.resolve_group_chat_url_for_payment",
                new=AsyncMock(return_value=None),
            ),
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            result = await ingest_crypto_payment(
                amount="50.00",
                token_symbol="USDC",
                chain="bsc",
                from_address=FROM_ADDRESS,
                to_address="0x7063760294b901CF56b34BEB6275A641B5178CDa",
                transaction_hash="0xtx3",
                alert_name=ALERT_NAME_RT_AT_CC,
            )

        self.assertFalse(result.auto_bound)
        self.assertEqual(result.status, "unbound")


class CryptoManualBindUpsertTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_manual_bind_upserts_wallet_binding(self):
        payment = CryptoPayment(
            id=7,
            amount_cents=12200,
            token_symbol="USDC",
            chain="bsc",
            from_address=FROM_ADDRESS,
            to_address="0xdef",
            transaction_hash="0xtx",
            alert_name=ALERT_NAME_CLUBGTO,
            alert_scope=ALERT_SCOPE_CLUBGTO,
            notification_chat_id=NOTIF_CHAT_ID,
            notification_message_id=NOTIF_MSG_ID,
        )
        wallet_row = None

        def _query(model):
            q = MagicMock()
            if model is CryptoPayment:
                q.filter_by.return_value.one_or_none.return_value = payment
            elif model is CryptoWalletBinding:
                q.filter_by.return_value.one_or_none.return_value = wallet_row
            return q

        mock_session = MagicMock()
        mock_session.query.side_effect = _query

        def _add(obj):
            nonlocal wallet_row
            if isinstance(obj, CryptoWalletBinding):
                wallet_row = obj

        mock_session.add.side_effect = _add
        mock_session.flush = MagicMock()

        with (
            patch("bot.services.crypto_payments.get_db") as mock_get_db,
            patch(
                "bot.services.crypto_payments.resolve_bound_group",
                return_value=BindResult(
                    ok=True,
                    bound_group=BoundGroup(
                        telegram_chat_id=CHAT_ID,
                        club_id=CLUB_ID,
                        group_title=GROUP_TITLE,
                    ),
                ),
            ),
            patch(
                "bot.services.crypto_payments.resolve_display_group_title",
                return_value=GROUP_TITLE,
            ),
            patch(
                "bot.services.crypto_payments.resolve_group_chat_url_for_payment",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "bot.services.crypto_payments.edit_telegram_notification",
                new=AsyncMock(),
            ),
            patch(
                "bot.services.crypto_payments.validate_bind_alert_scope",
                return_value=None,
            ),
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            result = await bind_crypto_payment_by_id(
                payment_id=7,
                group_title_input=GROUP_TITLE,
                bound_by_telegram_user_id=493310710,
            )

        self.assertTrue(result.ok)
        self.assertIsNotNone(wallet_row)
        assert wallet_row is not None
        self.assertEqual(
            wallet_row.from_address_normalized,
            normalize_from_address(FROM_ADDRESS),
        )
        self.assertEqual(wallet_row.alert_scope, ALERT_SCOPE_CLUBGTO)
        self.assertEqual(wallet_row.telegram_chat_id, CHAT_ID)


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
