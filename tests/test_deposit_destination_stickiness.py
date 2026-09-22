"""Tests for Venmo/Cash App destination tag stickiness."""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers import deposit as dep
from bot.services import payment_method_binding as pmb


OVER_TIER = {
    "id": 2,
    "label": "Over $100",
    "min_amount": Decimal("101"),
    "max_amount": Decimal("2000"),
}

UNDER_TIER = {
    "id": 1,
    "label": "Under $100",
    "min_amount": Decimal("20"),
    "max_amount": Decimal("100"),
}

METHOD_CASHAPP = {
    "id": 4,
    "name": "Cashapp",
    "slug": "cashapp",
}

METHOD_VENMO = {
    "id": 5,
    "name": "Venmo",
    "slug": "venmo",
}

STRIPE_OVER = {
    "variant_id": 20,
    "variant_label": "Cashapp Stripe",
    "weight": 80,
    "response_type": "text",
    "response_text": "Stripe over\n\n{{hyperlink}}",
    "use_group_checkout_link": True,
    "group_checkout_provider": "stripe",
}

NATIVE_A = {
    "variant_id": 21,
    "variant_label": "Cashapp Account 1",
    "weight": 18,
    "response_type": "text",
    "response_text": "Cashapp: https://cash.app/$eduardok4444",
    "use_group_checkout_link": False,
}

NATIVE_B = {
    "variant_id": 22,
    "variant_label": "Cashapp Account 2",
    "weight": 18,
    "response_type": "text",
    "response_text": "Cashapp: https://cash.app/$otherhandle",
    "use_group_checkout_link": False,
}

NATIVE_A_OTHER_TIER = {
    "variant_id": 23,
    "variant_label": "Cashapp Account 1 alt",
    "weight": 100,
    "response_type": "text",
    "response_text": "Cashapp: https://cash.app/$EduardoK4444",
    "use_group_checkout_link": False,
}

VENMO_A = {
    "variant_id": 31,
    "variant_label": "Venmo A",
    "weight": 50,
    "response_type": "text",
    "response_text": "Venmo: https://venmo.com/u/alice",
    "use_group_checkout_link": False,
}

VENMO_B = {
    "variant_id": 32,
    "variant_label": "Venmo B",
    "weight": 50,
    "response_type": "text",
    "response_text": "Venmo: https://venmo.com/u/bob",
    "use_group_checkout_link": False,
}

STRIPE_UNDER = {
    "variant_id": 10,
    "variant_label": "Stripe under",
    "weight": 100,
    "response_type": "text",
    "response_text": "Stripe under\n\n{{hyperlink}}",
    "use_group_checkout_link": True,
    "group_checkout_provider": "stripe",
}


class DestinationStickinessPickTests(unittest.TestCase):
    def test_prefers_native_over_stripe_when_no_sticky(self):
        with (
            patch.object(dep, "get_tier_for_amount", return_value=OVER_TIER),
            patch.object(
                dep,
                "list_tier_variants",
                return_value=[STRIPE_OVER, NATIVE_A],
            ),
            patch.object(dep, "get_destination_stickiness", return_value=None),
            patch.object(
                dep, "_pick_weighted_variant_dicts", return_value=dict(NATIVE_A)
            ) as pick_mock,
        ):
            response_data, tier = dep._pick_deposit_variant_response(
                4,
                METHOD_CASHAPP,
                Decimal("150"),
                chat_id=-100123,
                method_slug="cashapp",
            )

        pick_mock.assert_called_once()
        args = pick_mock.call_args[0][0]
        self.assertEqual([v["variant_id"] for v in args], [21])
        self.assertFalse(dep._stripe_checkout_enabled(response_data))
        self.assertEqual(tier, OVER_TIER)
        self.assertIn("$eduardok4444", response_data["response_text"].lower())

    def test_sticky_same_tag_other_variant_ok(self):
        sticky = SimpleNamespace(destination_tag="$eduardok4444", variant_id=21)
        with (
            patch.object(dep, "get_tier_for_amount", return_value=OVER_TIER),
            patch.object(
                dep,
                "list_tier_variants",
                return_value=[NATIVE_B, NATIVE_A_OTHER_TIER, STRIPE_OVER],
            ),
            patch.object(dep, "get_destination_stickiness", return_value=sticky),
            patch.object(dep, "clear_destination_stickiness_fallback_warning"),
            patch.object(
                dep,
                "_pick_weighted_variant_dicts",
                return_value=dict(NATIVE_A_OTHER_TIER),
            ) as pick_mock,
        ):
            response_data, _tier = dep._pick_deposit_variant_response(
                4,
                METHOD_CASHAPP,
                Decimal("150"),
                chat_id=-100123,
                method_slug="cashapp",
            )

        pick_mock.assert_called_once()
        matched_ids = [v["variant_id"] for v in pick_mock.call_args[0][0]]
        self.assertEqual(matched_ids, [23])
        self.assertIn("$eduardok4444", response_data["response_text"].lower())
        self.assertNotIn(dep._STICKINESS_FALLBACK_KEY, response_data)

    def test_sticky_falls_back_to_other_native_tag(self):
        sticky = SimpleNamespace(destination_tag="$eduardok4444", variant_id=21)
        with (
            patch.object(dep, "get_tier_for_amount", return_value=OVER_TIER),
            patch.object(
                dep,
                "list_tier_variants",
                return_value=[NATIVE_B],
            ),
            patch.object(dep, "get_destination_stickiness", return_value=sticky),
            patch.object(dep, "list_method_variants", return_value=[]),
            patch.object(
                dep, "_pick_weighted_variant_dicts", return_value=dict(NATIVE_B)
            ) as pick_mock,
        ):
            response_data, tier = dep._pick_deposit_variant_response(
                4,
                METHOD_CASHAPP,
                Decimal("150"),
                chat_id=-100123,
                method_slug="cashapp",
            )

        pick_mock.assert_called_once()
        self.assertEqual([v["variant_id"] for v in pick_mock.call_args[0][0]], [22])
        self.assertEqual(tier, OVER_TIER)
        self.assertIn("$otherhandle", response_data["response_text"].lower())
        meta = response_data[dep._STICKINESS_FALLBACK_KEY]
        self.assertEqual(meta["bound_tag"], "$eduardok4444")
        self.assertEqual(meta["shown"], "$otherhandle")
        self.assertEqual(meta["reason"], "does not exist")

    def test_sticky_prefers_other_native_over_stripe(self):
        sticky = SimpleNamespace(destination_tag="$eduardok4444", variant_id=21)
        with (
            patch.object(dep, "get_tier_for_amount", return_value=OVER_TIER),
            patch.object(
                dep,
                "list_tier_variants",
                return_value=[STRIPE_OVER, NATIVE_B],
            ),
            patch.object(dep, "get_destination_stickiness", return_value=sticky),
            patch.object(dep, "list_method_variants", return_value=[]),
            patch.object(
                dep, "_pick_weighted_variant_dicts", return_value=dict(NATIVE_B)
            ) as pick_mock,
        ):
            response_data, _tier = dep._pick_deposit_variant_response(
                4,
                METHOD_CASHAPP,
                Decimal("150"),
                chat_id=-100123,
                method_slug="cashapp",
            )

        pick_mock.assert_called_once()
        self.assertEqual([v["variant_id"] for v in pick_mock.call_args[0][0]], [22])
        self.assertEqual(
            response_data[dep._STICKINESS_FALLBACK_KEY]["shown"], "$otherhandle"
        )

    def test_sticky_weight_zero_falls_back_with_reason(self):
        sticky = SimpleNamespace(destination_tag="$eduardok4444", variant_id=21)
        paused = {**NATIVE_A, "weight": 0}
        with (
            patch.object(dep, "get_tier_for_amount", return_value=OVER_TIER),
            patch.object(
                dep,
                "list_tier_variants",
                return_value=[paused, NATIVE_B],
            ),
            patch.object(dep, "get_destination_stickiness", return_value=sticky),
            patch.object(dep, "list_method_variants", return_value=[]),
            patch.object(
                dep, "_pick_weighted_variant_dicts", return_value=dict(NATIVE_B)
            ),
        ):
            response_data, _tier = dep._pick_deposit_variant_response(
                4,
                METHOD_CASHAPP,
                Decimal("150"),
                chat_id=-100123,
                method_slug="cashapp",
            )

        meta = response_data[dep._STICKINESS_FALLBACK_KEY]
        self.assertEqual(meta["shown"], "$otherhandle")
        self.assertEqual(
            meta["reason"], "weight 0 (inactive) in Over $100 ($101–$2000)"
        )

    def test_sticky_cashapp_falls_back_to_stripe_when_tag_unavailable(self):
        sticky = SimpleNamespace(destination_tag="$eduardok4444", variant_id=21)
        with (
            patch.object(dep, "get_tier_for_amount", return_value=UNDER_TIER),
            patch.object(
                dep,
                "list_tier_variants",
                return_value=[STRIPE_UNDER],
            ),
            patch.object(dep, "get_destination_stickiness", return_value=sticky),
            patch.object(
                dep,
                "list_method_variants",
                return_value=[{**NATIVE_A, "tier_id": 2}],
            ),
            patch.object(
                dep, "_pick_weighted_variant_dicts", return_value=dict(STRIPE_UNDER)
            ),
        ):
            response_data, _tier = dep._pick_deposit_variant_response(
                4,
                METHOD_CASHAPP,
                Decimal("50"),
                chat_id=-100123,
                method_slug="cashapp",
            )

        self.assertTrue(dep._stripe_checkout_enabled(response_data))
        meta = response_data[dep._STICKINESS_FALLBACK_KEY]
        self.assertEqual(meta["shown"], "Stripe")
        self.assertEqual(meta["reason"], "not in the amount tier Under $100 ($20–$100)")

    def test_sticky_cashapp_falls_back_to_tier_inherited_stripe(self):
        """Tier-level Stripe (variant link null) must still unblock sticky chats."""
        sticky = SimpleNamespace(destination_tag="$eduardok4444", variant_id=21)
        under_default = {
            "variant_id": 11,
            "variant_label": "Default",
            "weight": 100,
            "response_type": "text",
            "response_text": "Stripe under\n\n{{hyperlink}}",
            # Inherits Stripe from tier — ClubGTO Under $100 shape.
        }
        tier = {
            **UNDER_TIER,
            "use_group_checkout_link": True,
            "group_checkout_provider": "stripe",
        }
        with (
            patch.object(dep, "get_tier_for_amount", return_value=tier),
            patch.object(
                dep,
                "list_tier_variants",
                return_value=[under_default],
            ),
            patch.object(dep, "get_destination_stickiness", return_value=sticky),
            patch.object(
                dep,
                "list_method_variants",
                return_value=[{**NATIVE_A, "tier_id": 2}],
            ),
            patch.object(
                dep,
                "_pick_weighted_variant_dicts",
                side_effect=lambda variants: dict(variants[0]),
            ),
        ):
            response_data, _tier = dep._pick_deposit_variant_response(
                4,
                METHOD_CASHAPP,
                Decimal("100"),
                chat_id=-100123,
                method_slug="cashapp",
            )

        self.assertIsNotNone(response_data)
        self.assertTrue(dep._stripe_checkout_enabled(response_data))
        self.assertEqual(response_data[dep._STICKINESS_FALLBACK_KEY]["shown"], "Stripe")

    def test_stripe_only_when_no_native_available(self):
        with (
            patch.object(dep, "get_tier_for_amount", return_value=UNDER_TIER),
            patch.object(
                dep,
                "list_tier_variants",
                return_value=[STRIPE_UNDER],
            ),
            patch.object(dep, "get_destination_stickiness", return_value=None),
            patch.object(
                dep, "_pick_weighted_variant_dicts", return_value=dict(STRIPE_UNDER)
            ),
        ):
            response_data, _tier = dep._pick_deposit_variant_response(
                4,
                METHOD_CASHAPP,
                Decimal("50"),
                chat_id=-100123,
                method_slug="cashapp",
            )

        self.assertTrue(dep._stripe_checkout_enabled(response_data))

    def test_venmo_uses_linking_variant_when_no_display_sticky(self):
        binding = SimpleNamespace(variant_id=31)
        with (
            patch.object(dep, "get_tier_for_amount", return_value=OVER_TIER),
            patch.object(
                dep,
                "list_tier_variants",
                return_value=[VENMO_A, VENMO_B],
            ),
            patch.object(dep, "get_destination_stickiness", return_value=None),
            patch.object(dep, "get_chat_binding", return_value=binding),
            patch.object(dep, "_pick_weighted_variant_dicts") as pick_mock,
        ):
            response_data, _tier = dep._pick_deposit_variant_response(
                5,
                METHOD_VENMO,
                Decimal("150"),
                chat_id=-100123,
                method_slug="venmo",
            )

        pick_mock.assert_not_called()
        self.assertEqual(response_data.get("variant_id"), 31)

    def test_venmo_sticky_falls_back_to_other_tag(self):
        sticky = SimpleNamespace(destination_tag="@alice", variant_id=31)
        with (
            patch.object(dep, "get_tier_for_amount", return_value=OVER_TIER),
            patch.object(
                dep,
                "list_tier_variants",
                return_value=[VENMO_B],
            ),
            patch.object(dep, "get_destination_stickiness", return_value=sticky),
            patch.object(dep, "list_method_variants", return_value=[]),
            patch.object(
                dep, "_pick_weighted_variant_dicts", return_value=dict(VENMO_B)
            ),
        ):
            response_data, tier = dep._pick_deposit_variant_response(
                5,
                METHOD_VENMO,
                Decimal("150"),
                chat_id=-100123,
                method_slug="venmo",
            )

        self.assertEqual(response_data.get("variant_id"), 32)
        self.assertEqual(tier, OVER_TIER)
        meta = response_data[dep._STICKINESS_FALLBACK_KEY]
        self.assertEqual(meta["bound_tag"], "@alice")
        self.assertEqual(meta["shown"], "@bob")
        self.assertEqual(meta["reason"], "does not exist")

    def test_sticky_hides_method_when_no_alternative(self):
        sticky = SimpleNamespace(destination_tag="$eduardok4444", variant_id=21)
        with (
            patch.object(dep, "get_tier_for_amount", return_value=OVER_TIER),
            patch.object(dep, "list_tier_variants", return_value=[]),
            patch.object(dep, "get_destination_stickiness", return_value=sticky),
            patch.object(dep, "list_method_variants", return_value=[]),
        ):
            response_data, tier = dep._pick_deposit_variant_response(
                4,
                METHOD_CASHAPP,
                Decimal("150"),
                chat_id=-100123,
                method_slug="cashapp",
            )

        self.assertIsNone(response_data)
        self.assertEqual(tier, OVER_TIER)

    def test_filter_hides_blocked_method(self):
        methods = [METHOD_CASHAPP, {"id": 9, "name": "Zelle", "slug": "zelle"}]
        with patch.object(
            dep,
            "_pick_deposit_variant_response",
            return_value=(None, OVER_TIER),
        ):
            filtered = dep.filter_methods_for_destination_stickiness(
                -100123, methods, Decimal("150")
            )
        self.assertEqual([m["slug"] for m in filtered], ["zelle"])


class DestinationStickinessLockTests(unittest.TestCase):
    def test_lock_on_native_response(self):
        with patch.object(dep, "ensure_destination_stickiness") as ensure_mock:
            dep._maybe_lock_destination_stickiness(
                chat_id=-100123,
                club_id=2,
                method_slug="cashapp",
                response_data=dict(NATIVE_A),
            )
        ensure_mock.assert_called_once()
        kwargs = ensure_mock.call_args.kwargs
        self.assertEqual(kwargs["destination_tag"], "$eduardok4444")
        self.assertEqual(kwargs["variant_id"], 21)

    def test_no_lock_on_stripe(self):
        with patch.object(dep, "ensure_destination_stickiness") as ensure_mock:
            dep._maybe_lock_destination_stickiness(
                chat_id=-100123,
                club_id=2,
                method_slug="cashapp",
                response_data=dict(STRIPE_OVER),
            )
        ensure_mock.assert_not_called()


class EnsureDestinationStickinessTests(unittest.TestCase):
    def test_insert_if_absent_does_not_overwrite(self):
        existing = SimpleNamespace(
            id=7,
            telegram_chat_id=-1001,
            club_id=2,
            payment_method_slug="cashapp",
            destination_tag="$eduardok4444",
            variant_id=21,
        )
        session = MagicMock()
        session.query.return_value.filter_by.return_value.one_or_none.return_value = (
            existing
        )
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch.object(pmb, "get_db", return_value=cm):
            row = pmb.ensure_destination_stickiness(
                telegram_chat_id=-1001,
                club_id=2,
                payment_method_slug="cashapp",
                destination_tag="$otherhandle",
                variant_id=99,
            )

        self.assertEqual(row.destination_tag, "$eduardok4444")
        session.add.assert_not_called()

    def test_insert_creates_when_missing(self):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.one_or_none.return_value = (
            None
        )
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch.object(pmb, "get_db", return_value=cm):
            row = pmb.ensure_destination_stickiness(
                telegram_chat_id=-1001,
                club_id=2,
                payment_method_slug="venmo",
                destination_tag="Alice",
                variant_id=31,
            )

        session.add.assert_called_once()
        self.assertEqual(row.destination_tag, "@alice")
        self.assertEqual(row.variant_id, 31)


class DestinationStickinessFallbackWarningTests(unittest.TestCase):
    def _session_with(self, row):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.one_or_none.return_value = row
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False
        return session, cm

    def test_claim_first_reason_then_dedupes(self):
        row = SimpleNamespace(
            id=7,
            telegram_chat_id=-1001,
            club_id=2,
            payment_method_slug="cashapp",
            destination_tag="$eduardok4444",
            variant_id=21,
            fallback_warned_reason=None,
        )
        _session, cm = self._session_with(row)
        with patch.object(pmb, "get_db", return_value=cm):
            first = pmb.claim_destination_stickiness_fallback_warning(
                -1001, "cashapp", "does not exist"
            )
            second = pmb.claim_destination_stickiness_fallback_warning(
                -1001, "cashapp", "does not exist"
            )
            third = pmb.claim_destination_stickiness_fallback_warning(
                -1001,
                "cashapp",
                "weight 0 (inactive) in Over $100 ($101–$2000)",
            )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(third)
        self.assertEqual(
            row.fallback_warned_reason,
            "weight 0 (inactive) in Over $100 ($101–$2000)",
        )

    def test_clear_resets_reason(self):
        row = SimpleNamespace(fallback_warned_reason="does not exist")
        _session, cm = self._session_with(row)
        with patch.object(pmb, "get_db", return_value=cm):
            pmb.clear_destination_stickiness_fallback_warning(-1001, "cashapp")
        self.assertIsNone(row.fallback_warned_reason)

    def test_claim_without_row_is_false(self):
        _session, cm = self._session_with(None)
        with patch.object(pmb, "get_db", return_value=cm):
            ok = pmb.claim_destination_stickiness_fallback_warning(
                -1001, "cashapp", "does not exist"
            )
        self.assertFalse(ok)


class DestinationStickinessFallbackNotifyTests(unittest.IsolatedAsyncioTestCase):
    async def test_notifies_once_via_claim(self):
        response = {
            dep._STICKINESS_FALLBACK_KEY: {
                "bound_tag": "$eduardok4444",
                "shown": "$otherhandle",
                "reason": "does not exist",
                "method_slug": "cashapp",
            }
        }
        with (
            patch.object(
                dep, "claim_destination_stickiness_fallback_warning", return_value=True
            ) as claim_mock,
            patch(
                "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
                new_callable=AsyncMock,
            ) as slack_mock,
        ):
            await dep._maybe_notify_destination_stickiness_fallback(
                response,
                chat_id=-100123,
                method_slug="cashapp",
                amount=Decimal("150"),
                title="Siddhartha | ClubGTO",
            )

        claim_mock.assert_called_once_with(-100123, "cashapp", "does not exist")
        slack_mock.assert_awaited_once()
        text = slack_mock.await_args.args[0]
        self.assertIn("Bound to `$eduardok4444` but shown `$otherhandle`", text)
        self.assertIn("Bound `$eduardok4444` was does not exist", text)
        self.assertEqual(
            slack_mock.await_args.kwargs["source"], "deposit_destination_fallback"
        )

    async def test_skips_slack_when_already_warned(self):
        response = {
            dep._STICKINESS_FALLBACK_KEY: {
                "bound_tag": "$eduardok4444",
                "shown": "Stripe",
                "reason": "does not exist",
                "method_slug": "cashapp",
            }
        }
        with (
            patch.object(
                dep, "claim_destination_stickiness_fallback_warning", return_value=False
            ),
            patch(
                "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
                new_callable=AsyncMock,
            ) as slack_mock,
        ):
            await dep._maybe_notify_destination_stickiness_fallback(
                response,
                chat_id=-100123,
                method_slug="cashapp",
            )

        slack_mock.assert_not_called()

    async def test_no_slack_without_fallback_meta(self):
        with (
            patch.object(dep, "claim_destination_stickiness_fallback_warning") as claim,
            patch(
                "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
                new_callable=AsyncMock,
            ) as slack_mock,
        ):
            await dep._maybe_notify_destination_stickiness_fallback(
                dict(NATIVE_A),
                chat_id=-100123,
                method_slug="cashapp",
            )

        claim.assert_not_called()
        slack_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
