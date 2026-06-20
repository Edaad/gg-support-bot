"""Tests for deposit flow hyperlink placeholder replacement."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import deposit as dep


def _deposit_query(*, chat_id=-100123, message_id=42):
    chat = SimpleNamespace(
        id=chat_id,
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=99)),
    )
    message = SimpleNamespace(chat=chat, message_id=message_id)
    return SimpleNamespace(message=message, edit_message_text=AsyncMock())


class DepositHyperlinkTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_stripe_hyperlink_placeholder_replaced_with_html_link(self):
        query = _deposit_query()
        chat = query.message.chat
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
        query = _deposit_query()
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
        query = _deposit_query()
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
                amount=dep.Decimal("100"),
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
        self.assertEqual(create_session.call_args.kwargs.get("checkout_preset_usd"), dep.Decimal("100"))

    async def test_stripe_checkout_uses_dashboard_min_max(self):
        query = _deposit_query()
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
            "checkout_min_amount": 25,
            "checkout_max_amount": 75,
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
        query = _deposit_query()
        chat = query.message.chat
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
        second_call = chat.send_message.await_args_list[1]
        second_text = second_call.kwargs.get("text") or second_call.args[0]
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
        query = _deposit_query()
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
        query = _deposit_query()
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
            "checkout_min_amount": 101,
            "checkout_max_amount": 2000,
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

    async def test_cashapp_below_100_uses_configured_variant_stripe(self):
        query = _deposit_query()
        context = SimpleNamespace(chat_data={"deposit_chat_id": -100123, "deposit_club_id": 2}, bot_data={})

        result = SimpleNamespace(
            checkout_url="https://checkout.stripe.com/cashapp",
            session_id="cs_cashapp",
            customer_id="cus_cashapp",
        )

        tier = {
            "response_type": "text",
            "response_text": "Use Stripe below $100\n\n{{hyperlink}}",
            "use_group_checkout_link": True,
            "group_checkout_provider": "stripe",
            "hyperlink_text": "PAY HERE",
            "min_amount": 20,
            "max_amount": 100,
        }
        method = {
            "response_type": "text",
            "response_text": "Method fallback\n\n{{hyperlink}}",
            "use_group_checkout_link": True,
            "group_checkout_provider": "stripe",
            "hyperlink_text": "PAY HERE",
        }
        variant = {
            "response_type": "text",
            "response_text": (
                "🚨 NO CREDIT CARDS. They will be refunded immediately\n\n"
                "• Enter your deposit amount on the checkout page ($20 minimum, $100 maximum).\n\n"
                "{{hyperlink}}"
            ),
            "use_group_checkout_link": True,
            "group_checkout_provider": "stripe",
            "hyperlink_text": "PAY HERE",
        }

        with (
            patch.object(dep, "stripe_configured", return_value=True),
            patch.object(dep, "create_stripe_checkout_session", return_value=result) as create_session,
            patch.object(dep, "send_response_messages", AsyncMock()) as send_response,
        ):
            merged = dep._with_method_checkout_settings(variant, method, tier=tier)
            ok = await dep._send_deposit_method_response(
                query,
                context,
                amount=dep.Decimal("78"),
                display_name="Cashapp",
                method_id=4,
                method_slug="cashapp",
                response_data=merged,
                method=method,
                tier=tier,
            )

        self.assertTrue(ok)
        create_session.assert_called_once()
        sent = send_response.await_args.args[1]
        self.assertIn("NO CREDIT CARDS", sent["response_text"])
        self.assertNotIn("_stripe_link_only_html", sent)

    async def test_cashapp_over_100_does_not_use_stripe_without_group_checkout(self):
        query = _deposit_query()
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


class DepositResponseContentTestCase(unittest.TestCase):
    def test_strip_legacy_emoji_does_not_wipe_entire_message(self):
        only_emoji = "• Please put a random emoji in the payment caption when sending"
        result = dep._strip_legacy_random_emoji_instruction(
            {"response_type": "text", "response_text": only_emoji},
            "zelle",
        )
        self.assertEqual(result["response_text"], only_emoji)

    def test_merge_response_layers_falls_back_to_tier(self):
        variant = {"response_type": "text", "response_text": None}
        tier = {
            "response_type": "text",
            "response_text": "Zelle: 310-567-0961\n\nSend screenshot when done.",
        }
        merged = dep._merge_response_layers(variant, tier)
        self.assertIn("310-567-0961", merged["response_text"])

    def test_prepare_deposit_response_data_uses_tier_copy(self):
        prepared = dep._prepare_deposit_response_data(
            {"response_type": "text", "response_text": None},
            method_slug="zelle",
            tier={
                "response_type": "text",
                "response_text": "Zelle: pay@example.com\nZelle Name: ACME",
            },
        )
        self.assertIn("pay@example.com", prepared["response_text"])

    def test_normalize_photo_type_with_text_only(self):
        normalized = dep._normalize_misconfigured_response_type(
            {
                "response_type": "photo",
                "response_text": "Zelle: 555-1234",
                "response_file_id": None,
            }
        )
        self.assertEqual(normalized["response_type"], "text")
        self.assertTrue(dep._response_data_has_content(normalized))


class DepositResponseContentAsyncTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_zelle_sends_instructions_when_variant_empty_tier_has_copy(self):
        chat = SimpleNamespace(id=-100123, send_message=AsyncMock())
        message = SimpleNamespace(chat=chat, message_id=42)
        query = SimpleNamespace(
            message=message,
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            chat_data={"deposit_chat_id": -100123, "deposit_club_id": 2},
            bot_data={},
        )

        response_data = {"response_type": "text", "response_text": None}
        tier = {
            "id": 7,
            "response_type": "text",
            "response_text": "Zelle: 310-567-0961\n\nPost a screenshot when done.",
        }

        with patch.object(dep, "send_response_messages", AsyncMock(return_value=[99])) as send_response:
            ok = await dep._send_deposit_method_response(
                query,
                context,
                amount=dep.Decimal("49"),
                display_name="Zelle",
                method_id=123,
                method_slug="zelle",
                response_data=response_data,
                tier=tier,
            )

        self.assertTrue(ok)
        send_response.assert_awaited_once()
        payload = send_response.await_args.args[1]
        self.assertIn("310-567-0961", payload["response_text"])


if __name__ == "__main__":
    unittest.main()

