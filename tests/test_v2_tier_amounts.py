"""Tests for v2 tier amount band validation."""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace

from api.payment_v2_helpers import (
    amounts_overlap,
    clamp_checkout_amount_bounds,
    sync_tier_checkout_bounds_from_band,
    sync_tier_checkout_bounds_to_variants,
    sync_method_envelope_side_effects,
    validate_all_method_tiers,
    validate_checkout_amount_bounds,
    validate_tier_amount_band,
    validate_tier_label,
    validate_variant_checkout_bounds,
)
from bot.services.payment_tier_order import select_tier_for_amount, tiers_in_match_order
from fastapi import HTTPException


def _method(min_amount=None, max_amount=None):
    return SimpleNamespace(min_amount=min_amount, max_amount=max_amount, tiers=[])


def _tier(id_, label, min_amount=None, max_amount=None, sort_order=0, **extra):
    return SimpleNamespace(
        id=id_,
        label=label,
        min_amount=min_amount,
        max_amount=max_amount,
        sort_order=sort_order,
        **extra,
    )


class TierAmountBandTestCase(unittest.TestCase):
    def test_adjacent_bands_do_not_overlap(self):
        self.assertFalse(
            amounts_overlap(
                Decimal("20"), Decimal("100"), Decimal("101"), Decimal("2000")
            )
        )

    def test_overlapping_bands_detected(self):
        self.assertTrue(
            amounts_overlap(
                Decimal("20"), Decimal("150"), Decimal("101"), Decimal("2000")
            )
        )

    def test_tier_below_method_min_rejected(self):
        method = _method(min_amount=Decimal("20"), max_amount=Decimal("2000"))
        siblings = [_tier(1, "Under $100", Decimal("20"), Decimal("100"))]
        with self.assertRaises(HTTPException) as ctx:
            validate_tier_amount_band(
                method,
                Decimal("10"),
                Decimal("50"),
                siblings,
                tier_label="Over $50",
            )
        self.assertIn("below method absolute minimum", str(ctx.exception.detail))

    def test_overlap_with_sibling_rejected(self):
        method = _method(min_amount=Decimal("20"), max_amount=None)
        siblings = [_tier(1, "Under $100", Decimal("20"), Decimal("100"))]
        with self.assertRaises(HTTPException) as ctx:
            validate_tier_amount_band(
                method,
                Decimal("50"),
                Decimal("150"),
                siblings,
                tier_label="Mid",
            )
        self.assertIn("overlaps", str(ctx.exception.detail).lower())

    def test_default_tier_band_within_envelope_allowed(self):
        method = _method(min_amount=Decimal("20"), max_amount=Decimal("2000"))
        siblings = [_tier(2, "Over $500", Decimal("501"), Decimal("2000"))]
        validate_tier_amount_band(
            method,
            Decimal("20"),
            Decimal("500"),
            siblings,
            tier_label="Default",
        )

    def test_default_tier_may_overlap_specific_tier(self):
        """The fallback tier covers what no specific tier claims, so overlap is fine."""
        method = _method(min_amount=Decimal("20"), max_amount=Decimal("2000"))
        siblings = [_tier(2, "Mid", Decimal("100"), Decimal("500"))]
        validate_tier_amount_band(
            method,
            Decimal("50"),
            Decimal("150"),
            siblings,
            tier_label="Default",
        )

    def test_specific_tier_may_sit_inside_unbounded_default(self):
        method = _method(min_amount=Decimal("100"), max_amount=None)
        default = _tier(13, "Default", Decimal("100"), None)
        method.tiers = [default]
        validate_tier_amount_band(
            method,
            Decimal("100"),
            Decimal("9999"),
            method.tiers,
            tier_label="$100+",
        )

    def test_specific_tiers_still_cannot_overlap_each_other(self):
        method = _method(min_amount=Decimal("100"), max_amount=None)
        siblings = [
            _tier(13, "Default", Decimal("100"), None),
            _tier(232, "$100+", Decimal("100"), Decimal("9999")),
        ]
        with self.assertRaises(HTTPException) as ctx:
            validate_tier_amount_band(
                method,
                Decimal("500"),
                Decimal("800"),
                siblings,
                tier_label="$500-800",
            )
        self.assertIn("$100+", str(ctx.exception.detail))

    def test_live_overlap_no_longer_blocks_method_save(self):
        """Round Table / Creator Club Venmo shape: Default 100+ alongside $100-9999."""
        default = _tier(
            13,
            "Default",
            Decimal("100"),
            None,
            checkout_min_amount=None,
            checkout_max_amount=None,
            variants=[],
        )
        over = _tier(
            232,
            "$100+",
            Decimal("100"),
            Decimal("9999"),
            checkout_min_amount=Decimal("100"),
            checkout_max_amount=Decimal("9999"),
            variants=[],
        )
        method = SimpleNamespace(
            min_amount=Decimal("50"), max_amount=None, tiers=[default, over]
        )
        sync_method_envelope_side_effects(method)
        validate_all_method_tiers(method)
        self.assertEqual(default.min_amount, Decimal("50"))

    def test_second_default_tier_rejected(self):
        siblings = [_tier(13, "Default", Decimal("100"), None)]
        with self.assertRaises(HTTPException) as ctx:
            validate_tier_label("Default", siblings)
        self.assertIn("already has", str(ctx.exception.detail))
        validate_tier_label("Default", siblings, exclude_tier_id=13)
        validate_tier_label("$100+", siblings)

    def test_raising_method_min_past_default_max_does_not_collapse_band(self):
        default = _tier(
            1,
            "Default",
            Decimal("100"),
            Decimal("499"),
            checkout_min_amount=None,
            checkout_max_amount=None,
            variants=[],
        )
        method = SimpleNamespace(
            min_amount=Decimal("600"), max_amount=Decimal("5000"), tiers=[default]
        )
        sync_method_envelope_side_effects(method)
        self.assertEqual(default.min_amount, Decimal("600"))
        self.assertEqual(default.max_amount, Decimal("5000"))


class TierMatchOrderTestCase(unittest.TestCase):
    def _venmo_tiers(self):
        return [
            _tier(13, "Default", Decimal("100"), None),
            _tier(232, "$100+", Decimal("100"), Decimal("9999")),
        ]

    def test_specific_tier_wins_over_fallback(self):
        tiers = self._venmo_tiers()
        self.assertEqual(select_tier_for_amount(tiers, Decimal("150")).label, "$100+")

    def test_fallback_catches_amount_outside_specific_bands(self):
        tiers = self._venmo_tiers()
        self.assertEqual(
            select_tier_for_amount(tiers, Decimal("12000")).label, "Default"
        )

    def test_no_tier_below_every_band(self):
        tiers = self._venmo_tiers()
        self.assertIsNone(select_tier_for_amount(tiers, Decimal("50")))

    def test_fallback_is_last_in_match_order(self):
        tiers = self._venmo_tiers()
        self.assertEqual(
            [t.label for t in tiers_in_match_order(tiers)], ["$100+", "Default"]
        )

    def test_sort_order_breaks_ties_between_specific_tiers(self):
        tiers = [
            _tier(1, "Default", Decimal("20"), None),
            _tier(2, "Mid", Decimal("100"), Decimal("500"), sort_order=2),
            _tier(3, "Narrow", Decimal("100"), Decimal("200"), sort_order=1),
        ]
        self.assertEqual(select_tier_for_amount(tiers, Decimal("150")).label, "Narrow")


class VariantCheckoutBoundsTestCase(unittest.TestCase):
    def test_variant_checkout_below_tier_min_rejected(self):
        method = _method(min_amount=Decimal("50"), max_amount=Decimal("9999"))
        tier = _tier(
            1,
            "$500+",
            Decimal("500"),
            Decimal("1000"),
            checkout_min_amount=None,
            checkout_max_amount=None,
        )
        with self.assertRaises(HTTPException) as ctx:
            validate_variant_checkout_bounds(method, tier, Decimal("100"), None)
        self.assertIn("below the tier minimum", str(ctx.exception.detail))

    def test_variant_checkout_inside_tier_band_allowed(self):
        method = _method(min_amount=Decimal("50"), max_amount=Decimal("9999"))
        tier = _tier(
            1,
            "$500+",
            Decimal("500"),
            Decimal("1000"),
            checkout_min_amount=None,
            checkout_max_amount=None,
        )
        validate_variant_checkout_bounds(method, tier, Decimal("600"), Decimal("900"))


class CheckoutAmountBoundsTestCase(unittest.TestCase):
    def test_checkout_min_below_method_min_rejected(self):
        method = _method(min_amount=Decimal("100"), max_amount=None)
        with self.assertRaises(HTTPException) as ctx:
            validate_checkout_amount_bounds(method, Decimal("50"), None)
        self.assertIn("below method absolute minimum", str(ctx.exception.detail))

    def test_clamp_raises_checkout_min_to_method_min(self):
        method = _method(min_amount=Decimal("100"), max_amount=Decimal("500"))
        lo, hi = clamp_checkout_amount_bounds(method, Decimal("20"), Decimal("600"))
        self.assertEqual(lo, Decimal("100"))
        self.assertEqual(hi, Decimal("500"))

    def test_sync_clamps_variant_checkout_bounds(self):
        variant = SimpleNamespace(
            checkout_min_amount=Decimal("20"),
            checkout_max_amount=Decimal("2000"),
        )
        tier = _tier(
            1,
            "Default",
            Decimal("50"),
            Decimal("400"),
            checkout_min_amount=Decimal("20"),
            checkout_max_amount=None,
            variants=[variant],
        )
        method = SimpleNamespace(
            min_amount=Decimal("100"), max_amount=Decimal("500"), tiers=[tier]
        )
        sync_method_envelope_side_effects(method)
        self.assertEqual(tier.min_amount, Decimal("100"))
        self.assertEqual(tier.max_amount, Decimal("400"))
        self.assertEqual(variant.checkout_min_amount, Decimal("100"))
        self.assertEqual(variant.checkout_max_amount, Decimal("500"))
        validate_all_method_tiers(method)

    def test_lowering_method_min_updates_default_tier(self):
        default = _tier(
            1,
            "Default",
            Decimal("50"),
            Decimal("100"),
            checkout_min_amount=Decimal("50"),
            checkout_max_amount=None,
            variants=[],
        )
        over = _tier(
            2,
            "Over $100",
            Decimal("101"),
            Decimal("2000"),
            sort_order=1,
            checkout_min_amount=Decimal("101"),
            checkout_max_amount=None,
            variants=[],
        )
        method = SimpleNamespace(
            min_amount=Decimal("20"), max_amount=Decimal("2000"), tiers=[default, over]
        )
        sync_method_envelope_side_effects(method)
        self.assertEqual(default.min_amount, Decimal("20"))
        self.assertEqual(default.max_amount, Decimal("100"))
        self.assertEqual(default.checkout_min_amount, Decimal("20"))
        self.assertEqual(over.min_amount, Decimal("101"))
        self.assertEqual(over.max_amount, Decimal("2000"))
        validate_all_method_tiers(method)

    def test_raising_method_min_updates_default_tier(self):
        default = _tier(
            1,
            "Default",
            Decimal("20"),
            Decimal("100"),
            checkout_min_amount=Decimal("20"),
            checkout_max_amount=None,
            variants=[],
        )
        over = _tier(
            2,
            "Over $100",
            Decimal("101"),
            Decimal("2000"),
            sort_order=1,
            checkout_min_amount=Decimal("101"),
            checkout_max_amount=None,
            variants=[],
        )
        method = SimpleNamespace(
            min_amount=Decimal("50"), max_amount=Decimal("2000"), tiers=[default, over]
        )
        sync_method_envelope_side_effects(method)
        self.assertEqual(default.min_amount, Decimal("50"))
        self.assertEqual(default.max_amount, Decimal("100"))
        self.assertEqual(default.checkout_min_amount, Decimal("50"))
        self.assertEqual(over.min_amount, Decimal("101"))
        self.assertEqual(over.max_amount, Decimal("2000"))
        validate_all_method_tiers(method)

    def test_raising_method_max_does_not_expand_tiers(self):
        default = _tier(
            1,
            "Default",
            Decimal("20"),
            Decimal("100"),
            checkout_min_amount=None,
            checkout_max_amount=Decimal("100"),
            variants=[],
        )
        method = SimpleNamespace(
            min_amount=Decimal("20"), max_amount=Decimal("500"), tiers=[default]
        )
        sync_method_envelope_side_effects(method)
        self.assertEqual(default.max_amount, Decimal("100"))
        self.assertEqual(default.checkout_max_amount, Decimal("100"))

    def test_lowering_method_max_clamps_tier_max(self):
        default = _tier(
            1,
            "Default",
            Decimal("20"),
            Decimal("2000"),
            checkout_min_amount=None,
            checkout_max_amount=Decimal("2000"),
            variants=[],
        )
        method = SimpleNamespace(
            min_amount=Decimal("20"), max_amount=Decimal("500"), tiers=[default]
        )
        sync_method_envelope_side_effects(method)
        self.assertEqual(default.max_amount, Decimal("500"))
        self.assertEqual(default.checkout_max_amount, Decimal("500"))
        validate_all_method_tiers(method)


class TierCheckoutSyncTestCase(unittest.TestCase):
    def test_tier_max_change_updates_inheriting_variant_max(self):
        variant = SimpleNamespace(
            checkout_min_amount=Decimal("20"),
            checkout_max_amount=Decimal("50"),
        )
        tier = SimpleNamespace(
            min_amount=Decimal("20"),
            max_amount=Decimal("100"),
            checkout_min_amount=Decimal("20"),
            checkout_max_amount=None,
            variants=[variant],
        )
        method = SimpleNamespace(
            min_amount=Decimal("20"), max_amount=Decimal("10000"), tiers=[tier]
        )
        sync_tier_checkout_bounds_to_variants(
            tier,
            method,
            prior_min=Decimal("20"),
            prior_max=Decimal("50"),
            prior_checkout_min=Decimal("20"),
            prior_checkout_max=None,
        )
        self.assertEqual(variant.checkout_min_amount, Decimal("20"))
        self.assertEqual(variant.checkout_max_amount, Decimal("100"))

    def test_custom_variant_max_not_overwritten(self):
        variant = SimpleNamespace(
            checkout_min_amount=Decimal("20"),
            checkout_max_amount=Decimal("500"),
        )
        tier = SimpleNamespace(
            min_amount=Decimal("20"),
            max_amount=Decimal("100"),
            checkout_min_amount=Decimal("20"),
            checkout_max_amount=None,
            variants=[variant],
        )
        method = SimpleNamespace(
            min_amount=Decimal("20"), max_amount=Decimal("10000"), tiers=[tier]
        )
        sync_tier_checkout_bounds_to_variants(
            tier,
            method,
            prior_min=Decimal("20"),
            prior_max=Decimal("50"),
            prior_checkout_min=Decimal("20"),
            prior_checkout_max=None,
        )
        self.assertEqual(variant.checkout_max_amount, Decimal("500"))

    def test_tier_checkout_from_band_updates_null_max(self):
        tier = SimpleNamespace(
            min_amount=Decimal("20"),
            max_amount=Decimal("100"),
            checkout_min_amount=Decimal("20"),
            checkout_max_amount=Decimal("50"),
            variants=[],
        )
        sync_tier_checkout_bounds_from_band(
            tier,
            prior_min=Decimal("20"),
            prior_max=Decimal("50"),
            prior_checkout_min=Decimal("20"),
            prior_checkout_max=Decimal("50"),
        )
        self.assertEqual(tier.checkout_max_amount, Decimal("100"))


if __name__ == "__main__":
    unittest.main()
