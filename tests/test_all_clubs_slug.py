"""Tests for all-clubs audit slug constants."""

from __future__ import annotations

import unittest

from api.club_slug import ALL_CLUBS_RECONCILE_UNITS, ALL_CLUBS_TRADE_SLUGS


class AllClubsSlugTestCase(unittest.TestCase):
    def test_trade_slugs_all_four(self):
        self.assertEqual(
            ALL_CLUBS_TRADE_SLUGS,
            ("round-table", "aces-table", "clubgto", "creator-club"),
        )

    def test_reconcile_units_three(self):
        self.assertEqual(
            ALL_CLUBS_RECONCILE_UNITS,
            ("round-table", "clubgto", "creator-club"),
        )


if __name__ == "__main__":
    unittest.main()
