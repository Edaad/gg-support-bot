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
    validate_checkout_amount_bounds,
    validate_tier_amount_band,
)
from fastapi import HTTPException


def _method(min_amount=None, max_amount=None):
    return SimpleNamespace(min_amount=min_amount, max_amount=max_amount, tiers=[])


def _tier(id_, label, min_amount=None, max_amount=None):
    return SimpleNamespace(id=id_, label=label, min_amount=min_amount, max_amount=max_amount)


class TierAmountBandTestCase(unittest.TestCase):
    def test_adjacent_bands_do_not_overlap(self):
        self.assertFalse(amounts_overlap(Decimal("20"), Decimal("100"), Decimal("101"), Decimal("2000")))

    def test_overlapping_bands_detected(self):
        self.assertTrue(amounts_overlap(Decimal("20"), Decimal("150"), Decimal("101"), Decimal("2000")))

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

    def test_default_tier_overlap_rejected(self):
        method = _method(min_amount=Decimal("20"), max_amount=Decimal("2000"))
        siblings = [_tier(2, "Mid", Decimal("100"), Decimal("500"))]
        with self.assertRaises(HTTPException) as ctx:
            validate_tier_amount_band(
                method,
                Decimal("50"),
                Decimal("150"),
                siblings,
                tier_label="Default",
            )
        self.assertIn("overlaps", str(ctx.exception.detail).lower())


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
        tier = SimpleNamespace(
            label="Default",
            min_amount=Decimal("50"),
            max_amount=Decimal("400"),
            checkout_min_amount=Decimal("20"),
            checkout_max_amount=None,
            variants=[variant],
        )
        method = SimpleNamespace(min_amount=Decimal("100"), max_amount=Decimal("500"), tiers=[tier])
        sync_method_envelope_side_effects(method)
        self.assertEqual(tier.min_amount, Decimal("50"))
        self.assertEqual(tier.max_amount, Decimal("400"))
        self.assertEqual(variant.checkout_min_amount, Decimal("100"))
        self.assertEqual(variant.checkout_max_amount, Decimal("500"))


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
        method = SimpleNamespace(min_amount=Decimal("20"), max_amount=Decimal("10000"), tiers=[tier])
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
        method = SimpleNamespace(min_amount=Decimal("20"), max_amount=Decimal("10000"), tiers=[tier])
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
