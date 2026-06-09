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


def _merged_stripe_over():
    return dep._merged_deposit_variant_response(
        dict(STRIPE_OVER_VARIANT), METHOD, tier=OVER_TIER
    )


def _merged_account_over():
    return dep._merged_deposit_variant_response(
        dict(ACCOUNT_VARIANT), METHOD, tier=OVER_TIER
    )


def _merged_stripe_under():
    return dep._merged_deposit_variant_response(
        dict(STRIPE_UNDER_VARIANT), METHOD, tier=UNDER_TIER
    )


class PickDepositVariantTestCase(unittest.TestCase):
    def test_weighted_pick_can_return_stripe_variant(self):
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
            )

        pick_mock.assert_called_once()
        self.assertTrue(dep._stripe_checkout_enabled(response_data))
        self.assertEqual(tier, OVER_TIER)

    def test_cashapp_normal_flow_ignores_sticky_binding(self):
        binding = SimpleNamespace(variant_id=21)
        with (
            patch.object(dep, "get_tier_for_amount", return_value=OVER_TIER),
            patch.object(dep, "get_chat_binding", return_value=binding),
            patch.object(
                dep,
                "pick_variant",
                return_value=dict(STRIPE_OVER_VARIANT),
            ) as pick_mock,
        ):
            dep._pick_deposit_variant_response(
                4,
                METHOD,
                Decimal("150"),
                chat_id=-100123,
                method_slug="cashapp",
            )

        pick_mock.assert_called_once_with(4, tier_id=OVER_TIER["id"], variant_id=None)

    def test_venmo_still_uses_sticky_binding(self):
        binding = SimpleNamespace(variant_id=99)
        venmo_method = {**METHOD, "slug": "venmo", "name": "Venmo"}
        with (
            patch.object(dep, "get_tier_for_amount", return_value=OVER_TIER),
            patch.object(dep, "get_chat_binding", return_value=binding),
            patch.object(dep, "pick_variant", return_value={"variant_id": 99}) as pick_mock,
        ):
            dep._pick_deposit_variant_response(
                4,
                venmo_method,
                Decimal("150"),
                chat_id=-100123,
                method_slug="venmo",
            )

        pick_mock.assert_called_once_with(4, tier_id=OVER_TIER["id"], variant_id=99)


class FirstTimeSetupFromChoiceTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_stripe_weighted_pick_skips_linking(self):
        query = SimpleNamespace(
            message=SimpleNamespace(chat=SimpleNamespace(id=-100123)),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            chat_data={
                "deposit_amount": Decimal("150"),
                "deposit_club_id": 2,
            }
        )
        merged = _merged_stripe_over()

        with (
            patch.object(
                dep,
                "_pick_deposit_variant_response",
                return_value=(merged, OVER_TIER),
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

        normal_mock.assert_awaited_once_with(
            query,
            context,
            method_id=4,
            method=METHOD,
            method_slug="cashapp",
            picked=(merged, OVER_TIER),
        )
        setup_mock.assert_not_awaited()
        self.assertEqual(result, dep.ConversationHandler.END)

    async def test_manual_weighted_pick_runs_linking(self):
        query = SimpleNamespace(
            message=SimpleNamespace(chat=SimpleNamespace(id=-100123)),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            chat_data={
                "deposit_amount": Decimal("150"),
                "deposit_club_id": 2,
            }
        )
        merged = _merged_account_over()

        with (
            patch.object(
                dep,
                "_pick_deposit_variant_response",
                return_value=(merged, OVER_TIER),
            ),
            patch.object(
                dep,
                "_run_normal_deposit_from_choice",
                new_callable=AsyncMock,
            ) as normal_mock,
            patch.object(
                dep,
                "_send_first_time_method_setup",
                new_callable=AsyncMock,
                return_value="await_ack",
            ) as setup_mock,
        ):
            result = await dep._run_first_time_method_setup_from_choice(
                query,
                context,
                method_id=4,
                method=METHOD,
                method_slug="cashapp",
                bind_kind=dep.BIND_KIND_SPECIAL_AMOUNT,
            )

        setup_mock.assert_awaited_once()
        normal_mock.assert_not_awaited()
        self.assertEqual(result, dep.DEPOSIT_SETUP_ACK)

    async def test_under_100_stripe_pick_skips_linking(self):
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
        merged = _merged_stripe_under()

        with (
            patch.object(
                dep,
                "_pick_deposit_variant_response",
                return_value=(merged, UNDER_TIER),
            ),
            patch.object(
                dep,
                "_run_normal_deposit_from_choice",
                new_callable=AsyncMock,
                return_value=dep.ConversationHandler.END,
            ) as normal_mock,
            patch.object(dep, "_send_first_time_method_setup", new_callable=AsyncMock) as setup_mock,
        ):
            await dep._run_first_time_method_setup_from_choice(
                query,
                context,
                method_id=4,
                method=METHOD,
                method_slug="cashapp",
                bind_kind=dep.BIND_KIND_SPECIAL_AMOUNT,
            )

        normal_mock.assert_awaited_once()
        setup_mock.assert_not_awaited()
