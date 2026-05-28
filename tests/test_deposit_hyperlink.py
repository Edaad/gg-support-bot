"""Tests for deposit flow hyperlink placeholder replacement."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import deposit as dep


class DepositHyperlinkTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_stripe_hyperlink_placeholder_replaced_with_html_link(self):
        chat = SimpleNamespace(send_message=AsyncMock())
        message = SimpleNamespace(chat=chat)
        query = SimpleNamespace(
            message=message,
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(chat_data={"deposit_chat_id": -100123, "deposit_club_id": 2}, bot_data={})

        result = SimpleNamespace(
            checkout_url="https://checkout.stripe.com/test?x=1&y=2",
            session_id="cs_test",
            customer_id="cus_test",
        )

        response_data = {
            "response_type": "text",
            "response_text": "Line1\n\n{{hyperlink}}\n\nLine3",
            "use_group_checkout_link": True,
            "group_checkout_provider": "stripe",
            "hyperlink_text": "PAY HERE",
        }

        with (
            patch.object(dep, "_is_stripe_like_slug", return_value=False),
            patch.object(dep, "stripe_configured", return_value=True),
            patch.object(dep, "create_stripe_checkout_session", return_value=result),
        ):
            ok = await dep._send_deposit_method_response(
                query,
                context,
                amount="?",
                display_name="Debit Card",
                method_id=123,
                method_slug="cashapp",
                response_data=response_data,
            )

        self.assertTrue(ok)
        self.assertTrue(query.edit_message_text.called)
        chat.send_message.assert_awaited()
        sent_text = chat.send_message.call_args.args[0]
        self.assertIn('<a href="https://checkout.stripe.com/test?x=1&amp;y=2">PAY HERE</a>', sent_text)
        self.assertEqual(chat.send_message.call_args.kwargs.get("parse_mode"), "HTML")

    async def test_stripe_hyperlink_placeholder_appended_when_missing(self):
        chat = SimpleNamespace(send_message=AsyncMock())
        message = SimpleNamespace(chat=chat)
        query = SimpleNamespace(
            message=message,
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(chat_data={"deposit_chat_id": -100123, "deposit_club_id": 2}, bot_data={})

        result = SimpleNamespace(
            checkout_url="https://checkout.stripe.com/test",
            session_id="cs_test",
            customer_id="cus_test",
        )

        response_data = {
            "response_type": "text",
            "response_text": "Hello",
            "use_group_checkout_link": True,
            "group_checkout_provider": "stripe",
            "hyperlink_text": "PAY",
        }

        with (
            patch.object(dep, "_is_stripe_like_slug", return_value=False),
            patch.object(dep, "stripe_configured", return_value=True),
            patch.object(dep, "create_stripe_checkout_session", return_value=result),
        ):
            ok = await dep._send_deposit_method_response(
                query,
                context,
                amount="?",
                display_name="Stripe",
                method_id=123,
                method_slug="cashapp",
                response_data=response_data,
            )

        self.assertTrue(ok)
        sent_text = chat.send_message.call_args.args[0]
        self.assertIn("Hello", sent_text)
        self.assertIn('<a href="https://checkout.stripe.com/test">PAY</a>', sent_text)


if __name__ == "__main__":
    unittest.main()

