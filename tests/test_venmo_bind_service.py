"""Unit tests for Venmo payment bind by id."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.venmo_payments import BindResult, bind_venmo_payment_by_id


class VenmoBindByIdTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_payment_not_found(self):
        with patch("bot.services.venmo_payments.resolve_bound_group") as mock_resolve:
            mock_resolve.return_value = BindResult(
                ok=True,
                bound_group=MagicMock(
                    telegram_chat_id=-1,
                    club_id=1,
                    group_title="RT / 1 / A",
                ),
            )
            with patch("bot.services.venmo_payments.get_db") as mock_get_db:
                session = MagicMock()
                session.query.return_value.filter_by.return_value.one_or_none.return_value = None
                mock_get_db.return_value.__enter__.return_value = session

                result = await bind_venmo_payment_by_id(
                    payment_id=99,
                    group_title_input="RT / 1 / A",
                )

        self.assertFalse(result.ok)
        self.assertIn("not found", (result.error or "").lower())

    async def test_invalid_group_title(self):
        result = await bind_venmo_payment_by_id(
            payment_id=1,
            group_title_input="",
        )
        self.assertFalse(result.ok)

    async def test_bind_updates_notification(self):
        payment = MagicMock()
        payment.id = 1
        payment.payer_name = "Alice"
        payment.venmo_handle = "@alice"
        payment.goods_or_services = False
        payment.amount_cents = 10000
        payment.notification_chat_id = -100
        payment.notification_message_id = 42

        with patch("bot.services.venmo_payments.resolve_bound_group") as mock_resolve:
            mock_resolve.return_value = BindResult(
                ok=True,
                bound_group=MagicMock(
                    telegram_chat_id=-200,
                    club_id=1,
                    group_title="RT / 1234 / Alice",
                ),
            )
            with patch("bot.services.venmo_payments.get_db") as mock_get_db:
                session = MagicMock()
                session.query.return_value.filter_by.return_value.one_or_none.return_value = payment
                mock_get_db.return_value.__enter__.return_value = session
                with patch(
                    "bot.services.venmo_payments.resolve_display_group_title",
                    return_value="RT / 1234 / Alice",
                ):
                    with patch(
                        "bot.services.venmo_payments.edit_telegram_notification",
                        new=AsyncMock(),
                    ) as mock_edit:
                        result = await bind_venmo_payment_by_id(
                            payment_id=1,
                            group_title_input="RT / 1234 / Alice",
                        )

        self.assertTrue(result.ok)
        mock_edit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
