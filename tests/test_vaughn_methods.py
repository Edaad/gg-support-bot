"""Tests for Vaughn ClubGTO deposit method identification."""

from __future__ import annotations

import unittest
from decimal import Decimal

from api.audit_ledger import LedgerLine
from api.vaughn_methods import (
    is_vaughn_method,
    matching_source_label,
    owner_matching_source_options,
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

    def test_zelle_coachingstarship_email(self):
        self.assertTrue(
            is_vaughn_method(
                source="deposit_zelle",
                variant="CoachingStarship@gmail.com",
                club_slug="clubgto",
            )
        )

    def test_zelle_janvenmo_email(self):
        self.assertTrue(
            is_vaughn_method(
                source="deposit_zelle",
                variant="janvenmo@gmail.com (clubgto)",
                club_slug="clubgto",
            )
        )
        self.assertEqual(
            matching_source_label(
                source="deposit_zelle",
                variant="janvenmo@gmail.com (clubgto)",
                source_label="Zelle",
                method_owner="vaughn",
            ),
            "GTO Zelle",
        )

    def test_zelle_clubgto1234_email(self):
        self.assertTrue(
            is_vaughn_method(
                source="deposit_zelle",
                variant="clubgto1234@gmail.com",
                club_slug="clubgto",
            )
        )

    def test_zelle_gto_chase_bank_label(self):
        self.assertTrue(
            is_vaughn_method(
                source="deposit_zelle",
                variant="gto chase zelle",
                club_slug="clubgto",
            )
        )
        self.assertEqual(
            matching_source_label(
                source="deposit_zelle",
                variant="gto chase zelle",
                source_label="Zelle",
                method_owner="vaughn",
            ),
            "GTO Zelle",
        )

    def test_zelle_baileys_wells_fargo_bank_label(self):
        self.assertTrue(
            is_vaughn_method(
                source="deposit_zelle",
                variant="bailey's wells fargo",
                club_slug="clubgto",
            )
        )
        self.assertTrue(
            is_vaughn_method(
                source="deposit_zelle",
                variant="3105670961",
                club_slug="clubgto",
            )
        )
        self.assertEqual(
            matching_source_label(
                source="deposit_zelle",
                variant="bailey's wells fargo",
                source_label="Zelle",
                method_owner="vaughn",
            ),
            "GTO Zelle",
        )

    def test_zelle_memo_vaughn_on_clubgto(self):
        self.assertTrue(
            is_vaughn_method(
                source="deposit_zelle",
                variant="coachingg444@gmail.com",
                club_slug="clubgto",
                memo="For Vaughn account",
            )
        )
        self.assertEqual(
            matching_source_label(
                source="deposit_zelle",
                variant="coachingg444@gmail.com",
                source_label="Zelle",
                memo="VAUGHN",
                method_owner="vaughn",
            ),
            "GTO Zelle",
        )

    def test_zelle_memo_vaughn_other_club_false(self):
        self.assertFalse(
            is_vaughn_method(
                source="deposit_zelle",
                variant="coachingg444@gmail.com",
                club_slug="round-table",
                memo="vaughn",
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
    def test_owner_prefixes(self):
        self.assertEqual(
            matching_source_label(
                source="deposit_zelle",
                variant="2133729202",
                source_label="Zelle",
                method_owner="vaughn",
            ),
            "GTO Zelle",
        )
        self.assertEqual(
            matching_source_label(
                source="deposit_zelle",
                variant="coachingg444@gmail.com",
                source_label="Zelle",
                method_owner="round-table",
            ),
            "RT Zelle",
        )
        self.assertEqual(
            matching_source_label(
                source="deposit_venmo",
                variant="@handle",
                source_label="Venmo",
                method_owner="mateos",
            ),
            "Mateos Venmo",
        )
        self.assertEqual(
            matching_source_label(
                source="deposit_stripe",
                variant=None,
                method_owner="vaughn",
            ),
            "Stripe",
        )
        self.assertEqual(
            matching_source_label(
                source="deposit_crypto",
                variant="USDT",
                source_label="Crypto",
                method_owner="vaughn",
            ),
            "GTO Crypto",
        )
        self.assertEqual(
            matching_source_label(
                source="bonus",
                variant="promo",
                source_label="Bonus",
            ),
            "Bonus",
        )
        self.assertEqual(
            matching_source_label(
                source="cashout",
                variant=None,
                source_label="Cashout Venmo",
            ),
            "Cashout Venmo",
        )

    def test_without_method_owner_unprefixed(self):
        self.assertEqual(
            matching_source_label(
                source="deposit_zelle",
                variant="2133729202",
                source_label="Zelle",
            ),
            "Zelle",
        )

    def test_owner_matching_dropdown_options(self):
        opts = owner_matching_source_options()
        self.assertIn("GTO Zelle", opts)
        self.assertIn("RT Zelle", opts)
        self.assertIn("Mateos Zelle", opts)
        self.assertIn("GTO Venmo", opts)
        self.assertIn("RT Venmo", opts)
        self.assertIn("Mateos Venmo", opts)
        self.assertIn("Stripe", opts)
        self.assertIn("GTO Crypto", opts)
        self.assertIn("RT Crypto", opts)
        self.assertIn("Mateos Crypto", opts)
        self.assertNotIn("GTO Stripe", opts)
        self.assertNotIn("Zelle", opts)
        self.assertIn("Cashout Venmo", opts)
        self.assertIn("Vaughn Cashout Venmo", opts)
        self.assertNotIn("Chip Transfer (RT↔AT)", opts)
        self.assertIn("Free Play", opts)
        self.assertIn("GTO INC", opts)


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


class UnionDepositMatchingLabelTestCase(unittest.TestCase):
    def test_union_zelle_on_clubgto_has_no_rt_gto_prefix(self):
        self.assertEqual(
            matching_source_label(
                source="deposit_union_zelle",
                variant="zelle-pool",
                club_slug="clubgto",
                source_label="Union Zelle",
            ),
            "Union Zelle",
        )

    def test_owner_dropdown_includes_union_sources(self):
        options = owner_matching_source_options()
        self.assertIn("Union Zelle", options)
        self.assertIn("Union Cash App", options)
        self.assertIn("Union Apple Pay", options)


if __name__ == "__main__":
    unittest.main()
