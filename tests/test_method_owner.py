"""Tests for payment method_owner slugs."""

from __future__ import annotations

import unittest

from api.method_owner import (
    METHOD_OWNER_ROUND_TABLE,
    METHOD_OWNER_VAUGHN,
    infer_method_owner_for_backfill,
    normalize_method_owner,
)
from api.vaughn_methods import VAUGHN_VENMO_HANDLES, VAUGHN_ZELLE_RECIPIENTS


class MethodOwnerTestCase(unittest.TestCase):
    def test_normalize_accepts_all_slugs(self):
        self.assertEqual(normalize_method_owner("round-table"), "round-table")
        self.assertEqual(normalize_method_owner("VAUGHN"), "vaughn")
        self.assertEqual(normalize_method_owner(" mateos "), "mateos")

    def test_normalize_rejects_unknown(self):
        with self.assertRaises(ValueError):
            normalize_method_owner("unknown")

    def test_backfill_vaughn_zelle_recipient(self):
        recipient = next(iter(VAUGHN_ZELLE_RECIPIENTS))
        owner = infer_method_owner_for_backfill(
            source="deposit_zelle",
            variant=recipient,
            club_slug="",
            memo=None,
        )
        self.assertEqual(owner, METHOD_OWNER_VAUGHN)

    def test_backfill_vaughn_venmo_handle(self):
        handle = next(iter(VAUGHN_VENMO_HANDLES))
        owner = infer_method_owner_for_backfill(
            source="deposit_venmo",
            variant=f"@{handle}",
            club_slug="",
            memo=None,
        )
        self.assertEqual(owner, METHOD_OWNER_VAUGHN)

    def test_backfill_clubgto_crypto(self):
        owner = infer_method_owner_for_backfill(
            source="deposit_crypto",
            variant="USDC",
            club_slug="clubgto",
            memo=None,
        )
        self.assertEqual(owner, METHOD_OWNER_VAUGHN)

    def test_backfill_non_vaughn_defaults_round_table(self):
        owner = infer_method_owner_for_backfill(
            source="deposit_cashapp",
            variant="$somehandle",
            club_slug="round-table",
            memo=None,
        )
        self.assertEqual(owner, METHOD_OWNER_ROUND_TABLE)


if __name__ == "__main__":
    unittest.main()
