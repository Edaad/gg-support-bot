"""Tests for Vaughn ClubGTO deposit method identification."""

from __future__ import annotations

import unittest
from decimal import Decimal

from api.audit_ledger import LedgerLine
from api.vaughn_methods import (
    clubgto_matching_source_options,
    is_vaughn_method,
    matching_source_label,
    tally_vaughn_methods,
)


def _line(
    *,
    source: str,
    variant: str | None,
    amount: str = "-100",
) -> LedgerLine:
    return LedgerLine(
        gg_player_id="1111-2222",
        member_nickname="P",
        source=source,
        source_label=source,
        amount_signed=Decimal(amount),
        occurred_at_utc=None,
        external_id=f"{source}:1",
        variant=variant,
    )


class IsVaughnMethodTestCase(unittest.TestCase):
    def test_zelle_starship_email(self):
        self.assertTrue(
            is_vaughn_method(
                source="deposit_zelle",
                variant="Starship5vllc@gmail.com",
                club_slug="clubgto",
            )
        )
        self.assertTrue(
            is_vaughn_method(
                source="deposit_zelle",
                variant="Citizens V",
                club_slug="clubgto",
            )
        )

    def test_zelle_starship_digits(self):
        self.assertTrue(
            is_vaughn_method(
                source="deposit_zelle",
                variant="2133729202",
                club_slug="clubgto",
            )
        )

    def test_zelle_starship_dashed(self):
        self.assertTrue(
            is_vaughn_method(
                source="deposit_zelle",
                variant="213-372-9202",
                club_slug="round-table",
            )
        )

    def test_zelle_other_false(self):
        self.assertFalse(
            is_vaughn_method(
                source="deposit_zelle",
                variant="coachingg444@gmail.com",
                club_slug="clubgto",
            )
        )

    def test_venmo_janseashells(self):
        self.assertTrue(
            is_vaughn_method(
                source="deposit_venmo",
                variant="@janseashells",
                club_slug="clubgto",
            )
        )
        self.assertTrue(
            is_vaughn_method(
                source="deposit_venmo",
                variant="janseashells",
                club_slug="clubgto",
            )
        )

    def test_venmo_other_false(self):
        self.assertFalse(
            is_vaughn_method(
                source="deposit_venmo",
                variant="@club-round",
                club_slug="clubgto",
            )
        )

    def test_crypto_clubgto_only(self):
        self.assertTrue(
            is_vaughn_method(
                source="deposit_crypto",
                variant="USDT",
                club_slug="clubgto",
            )
        )
        self.assertFalse(
            is_vaughn_method(
                source="deposit_crypto",
                variant="USDT",
                club_slug="round-table",
            )
        )

    def test_stripe_clubgto_only(self):
        self.assertTrue(
            is_vaughn_method(
                source="deposit_stripe",
                variant=None,
                club_slug="clubgto",
            )
        )
        self.assertFalse(
            is_vaughn_method(
                source="deposit_stripe",
                variant=None,
                club_slug="round-table",
            )
        )


class MatchingSourceLabelTestCase(unittest.TestCase):
    def test_clubgto_prefixes(self):
        self.assertEqual(
            matching_source_label(
                source="deposit_zelle",
                variant="2133729202",
                club_slug="clubgto",
                source_label="Zelle",
            ),
            "GTO Zelle",
        )
        self.assertEqual(
            matching_source_label(
                source="deposit_zelle",
                variant="Starship5vllc@gmail.com",
                club_slug="clubgto",
                source_label="Zelle",
            ),
            "GTO Zelle",
        )
        self.assertEqual(
            matching_source_label(
                source="deposit_zelle",
                variant="coachingg444@gmail.com",
                club_slug="clubgto",
                source_label="Zelle",
            ),
            "RT Zelle",
        )
        self.assertEqual(
            matching_source_label(
                source="deposit_stripe",
                variant=None,
                club_slug="clubgto",
            ),
            "GTO Stripe",
        )
        self.assertEqual(
            matching_source_label(
                source="bonus",
                variant="promo",
                club_slug="clubgto",
                source_label="Bonus",
            ),
            "Bonus",
        )
        self.assertEqual(
            matching_source_label(
                source="cashout",
                variant=None,
                club_slug="clubgto",
                source_label="Cashout Venmo",
            ),
            "Cashout Venmo",
        )

    def test_other_clubs_unprefixed(self):
        self.assertEqual(
            matching_source_label(
                source="deposit_zelle",
                variant="2133729202",
                club_slug="round-table",
                source_label="Zelle",
            ),
            "Zelle",
        )

    def test_clubgto_dropdown_options(self):
        opts = clubgto_matching_source_options()
        self.assertIn("GTO Zelle", opts)
        self.assertIn("RT Zelle", opts)
        self.assertIn("GTO Venmo", opts)
        self.assertIn("RT Venmo", opts)
        self.assertIn("GTO Stripe", opts)
        self.assertIn("GTO Crypto", opts)
        self.assertNotIn("RT Stripe", opts)
        self.assertNotIn("Zelle", opts)
        self.assertIn("Cashout Venmo", opts)
        self.assertIn("Cashout Cash App", opts)
        self.assertIn("Cashout Zelle", opts)
        self.assertIn("Cashout Crypto", opts)
        self.assertIn("Cashout Revolut", opts)
        self.assertIn("Cashout PayPal", opts)
        self.assertIn("Cashout", opts)
        self.assertIn("Vaughn Cashout Venmo", opts)
        self.assertIn("Vaughn Cashout Cash App", opts)
        self.assertIn("Vaughn Cashout Zelle", opts)
        self.assertIn("Vaughn Cashout Crypto", opts)
        self.assertNotIn("Vaughn Cashout Revolut", opts)
        self.assertNotIn("GTO Cashout Venmo", opts)
        self.assertNotIn("Chip Transfer (RT↔AT)", opts)


class TallyVaughnMethodsTestCase(unittest.TestCase):
    def test_groups_and_sums_abs(self):
        lines = [
            _line(source="deposit_zelle", variant="2133729202", amount="-50"),
            _line(source="deposit_zelle", variant="213-372-9202", amount="-25"),
            _line(
                source="deposit_zelle",
                variant="Starship5vllc@gmail.com",
                amount="-15",
            ),
            _line(source="deposit_zelle", variant="Citizens V", amount="-10"),
            _line(source="deposit_venmo", variant="@janseashells", amount="-40"),
            _line(source="deposit_crypto", variant="USDT", amount="-10"),
            _line(source="deposit_zelle", variant="other", amount="-99"),
            _line(source="deposit_crypto", variant="SOL", amount="-5"),
            _line(source="deposit_stripe", variant=None, amount="-30"),
            _line(source="deposit_stripe", variant=None, amount="-20"),
        ]
        tallies = tally_vaughn_methods(lines, club_slug="clubgto")
        self.assertEqual(
            [(t.method_label, t.tag, t.count, t.total_usd) for t in tallies],
            [
                ("Zelle", "2133729202", 2, Decimal("75")),
                ("Zelle", "starship5vllc@gmail.com", 2, Decimal("25")),
                ("Venmo", "@janseashells", 1, Decimal("40")),
                ("Crypto", "(all ClubGTO)", 2, Decimal("15")),
                ("Stripe", "(all ClubGTO)", 2, Decimal("50")),
            ],
        )

    def test_empty_when_no_vaughn(self):
        lines = [_line(source="deposit_zelle", variant="other")]
        self.assertEqual(tally_vaughn_methods(lines, club_slug="clubgto"), [])


if __name__ == "__main__":
    unittest.main()
