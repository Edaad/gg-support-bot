"""Tests for v2 tier amount band validation."""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace

from api.payment_v2_helpers import amounts_overlap, validate_tier_amount_band
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

    def test_default_tier_must_match_method_envelope(self):
        method = _method(min_amount=Decimal("50"), max_amount=None)
        with self.assertRaises(HTTPException) as ctx:
            validate_tier_amount_band(
                method,
                Decimal("20"),
                Decimal("100"),
                [],
                tier_label="Default",
            )
        self.assertIn("Default tier", str(ctx.exception.detail))


if __name__ == "__main__":
    unittest.main()
