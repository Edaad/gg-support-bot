"""Tests for first-time deposit linking variant selection (exclude Stripe Cash App)."""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import deposit as dep


UNDER_TIER = {
    "id": 1,
    "label": "Under $100",
    "min_amount": Decimal("20"),
    "max_amount": Decimal("100"),
    "use_group_checkout_link": True,
    "group_checkout_provider": "stripe",
    "hyperlink_text": "PAY HERE",
    "checkout_min_amount": Decimal("20"),
    "checkout_max_amount": Decimal("100"),
}

OVER_TIER = {
    "id": 2,
    "label": "Over $100",
    "min_amount": Decimal("101"),
    "max_amount": Decimal("2000"),
    "use_group_checkout_link": True,
    "group_checkout_provider": "stripe",
    "hyperlink_text": "PAY HERE",
    "checkout_min_amount": Decimal("101"),
    "checkout_max_amount": Decimal("2000"),
}

METHOD = {
    "id": 4,
    "name": "Cashapp",
    "slug": "cashapp",
    "min_amount": Decimal("20"),
    "max_amount": Decimal("2000"),
}

STRIPE_UNDER_VARIANT = {
    "variant_id": 10,
    "variant_label": "Stripe Cashapp (below $100)",
    "weight": 100,
    "response_type": "text",
    "response_text": "Stripe below $100\n\n{{hyperlink}}",
}

STRIPE_OVER_VARIANT = {
    "variant_id": 20,
    "variant_label": "Cashapp Stripe",
    "weight": 80,
    "response_type": "text",
    "response_text": "Stripe over $100\n\n{{hyperlink}}",
    "use_group_checkout_link": True,
    "group_checkout_provider": "stripe",
    "hyperlink_text": "PAY HERE",
}

ACCOUNT_VARIANT = {
    "variant_id": 21,
    "variant_label": "Cashapp Account 1",
    "weight": 18,
    "response_type": "text",
    "response_text": "Cashapp: https://cash.app/$michaelc4444",
    "use_group_checkout_link": False,
}


class PickLinkableDepositVariantTestCase(unittest.TestCase):
    def test_under_100_stripe_only_returns_none(self):
        with (
            patch.object(dep, "get_tier_for_amount", return_value=UNDER_TIER),
            patch.object(
                dep,
                "list_tier_variants",
                return_value=[STRIPE_UNDER_VARIANT],
            ),
        ):
            response_data, tier = dep._pick_deposit_variant_response(
                4,
                METHOD,
                Decimal("50"),
                method_slug="cashapp",
                linkable_only=True,
            )

        self.assertIsNone(response_data)
        self.assertEqual(tier, UNDER_TIER)

    def test_over_100_picks_manual_account_variant(self):
        with (
            patch.object(dep, "get_tier_for_amount", return_value=OVER_TIER),
            patch.object(
                dep,
                "list_tier_variants",
                return_value=[STRIPE_OVER_VARIANT, ACCOUNT_VARIANT],
            ),
            patch.object(dep.random, "choices", return_value=[ACCOUNT_VARIANT]) as choices_mock,
        ):
            response_data, tier = dep._pick_deposit_variant_response(
                4,
                METHOD,
                Decimal("150"),
                method_slug="cashapp",
                linkable_only=True,
            )

        choices_mock.assert_called_once()
        self.assertEqual(choices_mock.call_args.args[0], [ACCOUNT_VARIANT])
        self.assertEqual(choices_mock.call_args.kwargs["weights"], [18])
        self.assertIsNotNone(response_data)
        self.assertFalse(dep._stripe_checkout_enabled(response_data))
        self.assertIn("cash.app/$michaelc4444", response_data["response_text"])
        self.assertEqual(tier, OVER_TIER)

    def test_linkable_only_false_uses_weighted_pick(self):
        with (
            patch.object(dep, "get_tier_for_amount", return_value=OVER_TIER),
            patch.object(
                dep,
                "pick_variant",
                return_value=dict(STRIPE_OVER_VARIANT),
            ) as pick_mock,
        ):
            response_data, tier = dep._pick_deposit_variant_response(
                4,
                METHOD,
                Decimal("150"),
                method_slug="cashapp",
                linkable_only=False,
            )

        pick_mock.assert_called_once()
        self.assertTrue(dep._stripe_checkout_enabled(response_data))
        self.assertEqual(tier, OVER_TIER)


class FirstTimeSetupFallthroughTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_falls_through_to_normal_deposit_when_only_stripe(self):
        query = SimpleNamespace(
            message=SimpleNamespace(chat=SimpleNamespace(id=-100123)),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            chat_data={
                "deposit_amount": Decimal("50"),
                "deposit_club_id": 2,
            }
        )

        with (
            patch.object(
                dep,
                "_pick_deposit_variant_response",
                return_value=(None, UNDER_TIER),
            ),
            patch.object(
                dep,
                "_run_normal_deposit_from_choice",
                new_callable=AsyncMock,
                return_value=dep.ConversationHandler.END,
            ) as normal_mock,
            patch.object(dep, "_send_first_time_method_setup", new_callable=AsyncMock) as setup_mock,
        ):
            result = await dep._run_first_time_method_setup_from_choice(
                query,
                context,
                method_id=4,
                method=METHOD,
                method_slug="cashapp",
                bind_kind=dep.BIND_KIND_SPECIAL_AMOUNT,
            )

        normal_mock.assert_awaited_once()
        setup_mock.assert_not_awaited()
        self.assertEqual(result, dep.ConversationHandler.END)
