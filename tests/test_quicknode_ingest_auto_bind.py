"""ingest_crypto_payment(allow_auto_bind=False): a single known wallet still waits for staff."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.crypto_payments import (
    ALERT_SCOPE_CLUBGTO,
    ingest_crypto_payment,
)
from bot.services.payment_bind_candidates import CandidateGroup
from db.models import CryptoPayment
from tests.support.ingest_mocks import start_payment_ingest_mocks, stop_patchers

CHAT_ID = -1001234567890
NOTIF = (-1009999999999, 12345)
TRON_TX = "70146ca6d949b2a6cff49191acb5e09b6de836b3317104e1b5af5e36c35318ea"


class AllowAutoBindTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._record_posts = patch(
            "notification.payment_notification_posts.record_payment_notification_posts"
        )
        self._record_posts.start()
        self._ingest_patchers = start_payment_ingest_mocks()

    def tearDown(self) -> None:
        stop_patchers(self._ingest_patchers)
        self._record_posts.stop()

    async def _ingest(self, **extra):
        candidate = CandidateGroup(
            telegram_chat_id=CHAT_ID, club_id=2, group_title="GTO / 1 / P"
        )
        payment_obj = CryptoPayment(
            id=99,
            amount_cents=2200,
            token_symbol="USDT",
            chain="tron",
            from_address="TN9mDLaQVzikxT5EjmFTmVGk7bVLmUXG1C",
            to_address="TZ9LgB7MQjvSmnPqGY1NYNbMh4fYbCdBtD",
            transaction_hash=TRON_TX,
            alert_name="QuickNode GTO tron",
            alert_scope=ALERT_SCOPE_CLUBGTO,
        )

        def _query(model):
            q = MagicMock()
            if model is CryptoPayment:
                q.filter_by.return_value.one_or_none.side_effect = [None]
                q.filter_by.return_value.one.return_value = payment_obj
            return q

        session = MagicMock()
        session.query.side_effect = _query

        def _add(obj):
            if isinstance(obj, CryptoPayment) and obj.id is None:
                obj.id = 99

        session.add.side_effect = _add
        auto_bind = MagicMock(return_value=candidate)
        deliver = AsyncMock(return_value=[NOTIF])
        with (
            patch("bot.services.crypto_payments.get_db") as get_db,
            patch(
                "notification.payment_notification_delivery.deliver_payment_notification",
                new=deliver,
            ),
            patch(
                "bot.services.crypto_payments.resolve_group_chat_url_for_payment",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "bot.services.payment_bind_candidates.candidates_for_payment",
                return_value=[candidate],
            ),
            patch(
                "notification.payment_bind_helpers.auto_bind_from_candidates", auto_bind
            ),
            patch("bot.services.crypto_payments.track_ingest_notification"),
            patch(
                "bot.services.crypto_payments.maybe_notify_player_on_auto_bound",
                new=AsyncMock(),
            ),
        ):
            get_db.return_value.__enter__ = MagicMock(return_value=session)
            get_db.return_value.__exit__ = MagicMock(return_value=False)
            result = await ingest_crypto_payment(
                amount="22.00",
                token_symbol="USDT",
                chain="tron",
                from_address=payment_obj.from_address,
                to_address=payment_obj.to_address,
                transaction_hash=TRON_TX,
                alert_name="QuickNode GTO tron",
                method_owner="vaughn",
                source_external_id=f"{TRON_TX}_0",
                **extra,
            )
        return result, auto_bind, deliver, payment_obj

    async def test_allow_auto_bind_false_keeps_single_match_unbound(self):
        result, auto_bind, deliver, payment = await self._ingest(allow_auto_bind=False)
        self.assertFalse(result.auto_bound)
        self.assertEqual(result.status, "unbound")
        auto_bind.assert_not_called()
        self.assertIsNone(payment.telegram_chat_id)
        # Staff still get the one-candidate picker.
        self.assertIsNotNone(deliver.await_args.kwargs["reply_markup"])

    async def test_default_still_auto_binds(self):
        result, auto_bind, _, _ = await self._ingest()
        self.assertTrue(result.auto_bound)
        auto_bind.assert_called_once()


if __name__ == "__main__":
    unittest.main()
