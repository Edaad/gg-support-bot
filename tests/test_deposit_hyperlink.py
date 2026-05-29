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
            patch.object(dep, "stripe_configured", return_value=True),
            patch.object(dep, "create_stripe_checkout_session", return_value=result) as create_session,
        ):
            ok = await dep._send_deposit_method_response(
                query,
                context,
                amount="?",
                display_name="Debit Card",
                method_id=123,
                method_slug="debitcard",
                response_data=response_data,
            )

        create_session.assert_called_once()
        self.assertEqual(create_session.call_args.kwargs.get("checkout_min_usd"), None)

        self.assertTrue(ok)
        self.assertTrue(query.edit_message_text.called)
        chat.send_message.assert_awaited()
        sent_text = chat.send_message.call_args.args[0]
        self.assertIn('<a href="https://checkout.stripe.com/test?x=1&amp;y=2">PAY HERE</a>', sent_text)
        self.assertEqual(chat.send_message.call_args.kwargs.get("parse_mode"), "HTML")

    async def test_stripe_checkout_when_provider_missing_defaults_stripe(self):
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
            "response_text": "{{hyperlink}}",
            "use_group_checkout_link": True,
            "group_checkout_provider": None,
            "hyperlink_text": "PAY",
        }

        with (
            patch.object(dep, "stripe_configured", return_value=True),
            patch.object(dep, "create_stripe_checkout_session", return_value=result) as create_session,
        ):
            ok = await dep._send_deposit_method_response(
                query,
                context,
                amount=dep.Decimal("50"),
                display_name="Cashapp",
                method_id=123,
                method_slug="cashapp",
                response_data=response_data,
            )

        self.assertTrue(ok)
        create_session.assert_called_once()

    async def test_tier_group_checkout_overrides_method(self):
        chat = SimpleNamespace(send_message=AsyncMock())
        message = SimpleNamespace(chat=chat)
        query = SimpleNamespace(
            message=message,
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(chat_data={"deposit_chat_id": -100123, "deposit_club_id": 2}, bot_data={})

        result = SimpleNamespace(
            checkout_url="https://checkout.stripe.com/tier",
            session_id="cs_tier",
            customer_id="cus_tier",
        )

        response_data = {
            "response_type": "text",
            "response_text": "Under 100\n\n{{hyperlink}}",
            "use_group_checkout_link": True,
            "group_checkout_provider": "stripe",
            "hyperlink_text": "Pay",
            "min_amount": 20,
            "max_amount": 100,
        }
        method = {
            "use_group_checkout_link": False,
            "group_checkout_provider": None,
            "min_amount": 5,
            "max_amount": 500,
        }

        with (
            patch.object(dep, "stripe_configured", return_value=True),
            patch.object(dep, "create_stripe_checkout_session", return_value=result) as create_session,
        ):
            merged = dep._with_method_checkout_settings(response_data, method, tier=response_data)
            ok = await dep._send_deposit_method_response(
                query,
                context,
                amount=dep.Decimal("50"),
                display_name="Cashapp",
                method_id=123,
                method_slug="cashapp",
                response_data=merged,
            )

        self.assertTrue(ok)
        self.assertTrue(dep._stripe_checkout_enabled(merged))
        create_session.assert_called_once()
        self.assertEqual(create_session.call_args.kwargs.get("checkout_min_usd"), 20)
        self.assertEqual(create_session.call_args.kwargs.get("checkout_max_usd"), 100)

    async def test_stripe_checkout_uses_dashboard_min_max(self):
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
            "response_text": "{{hyperlink}}",
            "use_group_checkout_link": True,
            "group_checkout_provider": "stripe",
            "hyperlink_text": "Pay",
            "min_amount": 25,
            "max_amount": 75,
        }

        with (
            patch.object(dep, "stripe_configured", return_value=True),
            patch.object(dep, "create_stripe_checkout_session", return_value=result) as create_session,
        ):
            ok = await dep._send_deposit_method_response(
                query,
                context,
                amount=dep.Decimal("50"),
                display_name="Card",
                method_id=123,
                method_slug="debitcard",
                response_data=response_data,
            )

        self.assertTrue(ok)
        create_session.assert_called_once()
        self.assertEqual(create_session.call_args.kwargs.get("checkout_min_usd"), 25)
        self.assertEqual(create_session.call_args.kwargs.get("checkout_max_usd"), 75)

    async def test_stripe_without_placeholder_sends_link_separately(self):
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
            patch.object(dep, "stripe_configured", return_value=True),
            patch.object(dep, "create_stripe_checkout_session", return_value=result),
        ):
            ok = await dep._send_deposit_method_response(
                query,
                context,
                amount="?",
                display_name="Stripe",
                method_id=123,
                method_slug="stripe",
                response_data=response_data,
            )

        self.assertTrue(ok)
        self.assertGreaterEqual(chat.send_message.await_count, 2)
        first_text = chat.send_message.await_args_list[0].args[0]
        second_text = chat.send_message.await_args_list[1].args[0]
        self.assertEqual(first_text, "Hello")
        self.assertIn('<a href="https://checkout.stripe.com/test">PAY</a>', second_text)

    async def test_variant_false_overrides_tier_stripe(self):
        variant = {
            "response_type": "text",
            "response_text": "cashapp static",
            "use_group_checkout_link": False,
        }
        method = {"use_group_checkout_link": False}
        tier = {
            "use_group_checkout_link": True,
            "group_checkout_provider": "stripe",
            "hyperlink_text": "PAY",
        }
        merged = dep._with_method_checkout_settings(variant, method, tier=tier)
        self.assertFalse(dep._stripe_checkout_enabled(merged))

    async def test_stripe_slug_without_group_checkout_uses_static_response(self):
        chat = SimpleNamespace(send_message=AsyncMock())
        message = SimpleNamespace(chat=chat)
        query = SimpleNamespace(
            message=message,
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(chat_data={"deposit_chat_id": -100123, "deposit_club_id": 2}, bot_data={})

        response_data = {
            "response_type": "text",
            "response_text": "stripe instructions",
            "use_group_checkout_link": False,
            "group_checkout_provider": None,
        }

        with (
            patch.object(dep, "stripe_configured", return_value=True),
            patch.object(dep, "create_stripe_checkout_session") as create_session,
            patch.object(dep, "send_response_messages", AsyncMock()) as send_response,
        ):
            ok = await dep._send_deposit_method_response(
                query,
                context,
                amount=dep.Decimal("50"),
                display_name="Stripe",
                method_id=123,
                method_slug="stripe",
                response_data=response_data,
            )

        self.assertTrue(ok)
        create_session.assert_not_called()
        send_response.assert_awaited()

    async def test_variant_group_checkout_overrides_tier(self):
        chat = SimpleNamespace(send_message=AsyncMock())
        message = SimpleNamespace(chat=chat)
        query = SimpleNamespace(
            message=message,
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(chat_data={"deposit_chat_id": -100123, "deposit_club_id": 2}, bot_data={})

        result = SimpleNamespace(
            checkout_url="https://checkout.stripe.com/variant",
            session_id="cs_variant",
            customer_id="cus_variant",
        )

        variant = {
            "response_type": "text",
            "response_text": "Stripe variant\n\n{{hyperlink}}",
            "use_group_checkout_link": True,
            "group_checkout_provider": "stripe",
            "hyperlink_text": "Pay now",
            "min_amount": 101,
            "max_amount": 2000,
        }
        method = {
            "use_group_checkout_link": False,
            "min_amount": 20,
            "max_amount": 100,
        }
        tier = {
            "use_group_checkout_link": False,
            "min_amount": 20,
            "max_amount": 100,
        }

        with (
            patch.object(dep, "stripe_configured", return_value=True),
            patch.object(dep, "create_stripe_checkout_session", return_value=result) as create_session,
        ):
            merged = dep._with_method_checkout_settings(variant, method, tier=tier)
            ok = await dep._send_deposit_method_response(
                query,
                context,
                amount=dep.Decimal("150"),
                display_name="Cashapp",
                method_id=123,
                method_slug="cashapp",
                response_data=merged,
            )

        self.assertTrue(ok)
        self.assertTrue(dep._stripe_checkout_enabled(merged))
        create_session.assert_called_once()
        self.assertEqual(create_session.call_args.kwargs.get("checkout_min_usd"), 101)
        self.assertEqual(create_session.call_args.kwargs.get("checkout_max_usd"), 2000)

    async def test_cashapp_over_100_does_not_use_stripe_without_group_checkout(self):
        chat = SimpleNamespace(send_message=AsyncMock())
        message = SimpleNamespace(chat=chat)
        query = SimpleNamespace(
            message=message,
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(chat_data={"deposit_chat_id": -100123, "deposit_club_id": 2}, bot_data={})

        response_data = {
            "response_type": "text",
            "response_text": "cashapp instructions",
            "use_group_checkout_link": False,
            "group_checkout_provider": None,
            "hyperlink_text": "PAY HERE",
        }

        with (
            patch.object(dep, "stripe_configured", return_value=True),
            patch.object(dep, "create_stripe_checkout_session") as create_session,
            patch.object(dep, "send_response_messages", AsyncMock()) as send_response,
        ):
            ok = await dep._send_deposit_method_response(
                query,
                context,
                amount=dep.Decimal("150"),
                display_name="Cashapp",
                method_id=123,
                method_slug="cashapp",
                response_data=response_data,
            )

        self.assertTrue(ok)
        create_session.assert_not_called()
        send_response.assert_awaited()


if __name__ == "__main__":
    unittest.main()

