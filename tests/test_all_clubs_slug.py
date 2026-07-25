"""Tests for all-clubs reconcile slug helpers."""

from __future__ import annotations

import unittest

from fastapi import HTTPException

from api.club_slug import (
    ALL_CLUBS_TRADE_SLUGS,
    reconcile_units_for_slug,
    trade_slugs_for_reconcile,
)


class AllClubsSlugTestCase(unittest.TestCase):
    def test_trade_slugs_all_four(self):
        self.assertEqual(
            trade_slugs_for_reconcile("all-clubs"),
            ALL_CLUBS_TRADE_SLUGS,
        )

    def test_reconcile_units_three(self):
        self.assertEqual(
            reconcile_units_for_slug("all-clubs"),
            ("round-table", "clubgto", "creator-club"),
        )

    def test_unknown_raises(self):
        with self.assertRaises(HTTPException):
            trade_slugs_for_reconcile("nope")


if __name__ == "__main__":
    unittest.main()
