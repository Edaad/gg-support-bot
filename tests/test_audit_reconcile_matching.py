"""Tests for best-effort trade ↔ ledger matching."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from api.audit_ledger import LedgerLine
from api.audit_reconcile import TradeLineForMatch
from api.audit_reconcile_matching import (
    CHIP_TRANSFER_AT_CC_LABEL,
    CHIP_TRANSFER_PLAYER_LABEL,
    CHIP_TRANSFER_RT_AT_LABEL,
    apply_chip_transfer_matches,
    match_trade_lines_to_ledger,
    round_whole_usd,
)


def _trade(
    *,
    line_id: int = 1,
    amount: str = "-100",
    gg_id: str | None = "1111-2222",
    nick: str | None = "PlayerOne",
    occurred: datetime | None = None,
    sheet_row: int = 1,
    club: str | None = "round-table",
) -> TradeLineForMatch:
    return TradeLineForMatch(
        line_id=line_id,
        occurred_at=occurred,
        amount=Decimal(amount),
        member_gg_player_id=gg_id,
        member_nickname=nick,
        sheet_row=sheet_row,
        trade_club_slug=club,
    )


def _ledger(
    *,
    source: str = "deposit_stripe",
    source_label: str = "Stripe",
    amount_signed: str = "-100",
    gg_id: str | None = "1111-2222",
    nick: str | None = "PlayerOne",
    occurred: datetime | None = None,
    external_id: str = "deposit_stripe:1",
    display_name: str | None = None,
    variant: str | None = None,
) -> LedgerLine:
    return LedgerLine(
        gg_player_id=gg_id,
        member_nickname=nick,
        source=source,
        source_label=source_label,
        amount_signed=Decimal(amount_signed),
        occurred_at_utc=occurred,
        external_id=external_id,
        display_name=display_name,
        variant=variant,
    )


class RoundWholeUsdTestCase(unittest.TestCase):
    def test_half_up(self):
        self.assertEqual(round_whole_usd(Decimal("99.50")), Decimal("100"))
        self.assertEqual(round_whole_usd(Decimal("100.49")), Decimal("100"))
        self.assertEqual(round_whole_usd(Decimal("-99.50")), Decimal("100"))


class MatchTradeLinesTestCase(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 7, 3, 6, 30, tzinfo=timezone.utc)

    def test_same_player_exact_match_consumes_event(self):
        trade = _trade(occurred=self.t0, amount="-100")
        ledger = _ledger(
            occurred=self.t0,
            amount_signed="-100",
            display_name="Jane Doe",
        )
        extra = _ledger(
            occurred=self.t0,
            amount_signed="-100",
            external_id="deposit_stripe:2",
            display_name="Other",
        )
        rows = match_trade_lines_to_ledger(
            [trade],
            [ledger, extra],
            club_slug="aces-table",
        ).rows
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].match_name, "Jane Doe")
        self.assertEqual(rows[0].match_source, "Stripe")
        self.assertEqual(rows[0].match_amount, Decimal("100"))
        self.assertEqual(rows[0].variant, "")
        self.assertFalse(rows[0].vaughn_method)

        trade2 = _trade(line_id=2, occurred=self.t0, amount="-100", sheet_row=2)
        rows2 = match_trade_lines_to_ledger(
            [trade, trade2],
            [ledger],
            club_slug="aces-table",
        ).rows
        self.assertEqual(rows2[0].match_name, "Jane Doe")
        self.assertEqual(rows2[1].match_name, "")
        self.assertEqual(rows2[1].match_source, "")

        leftover = match_trade_lines_to_ledger(
            [trade],
            [ledger, extra],
            club_slug="aces-table",
        )
        self.assertEqual(
            [line.external_id for line in leftover.unmatched_ledger],
            ["deposit_stripe:2"],
        )

    def test_fallback_amount_time_without_player_id(self):
        trade = _trade(gg_id="9999-0000", occurred=self.t0, amount="-50")
        ledger = _ledger(
            gg_id=None,
            nick=None,
            occurred=self.t0 + timedelta(minutes=2),
            amount_signed="-50",
            source="deposit_zelle",
            source_label="Zelle",
            display_name="Miah Xeshan",
            external_id="deposit_zelle:1",
            variant="gto zelle",
        )
        rows = match_trade_lines_to_ledger(
            [trade],
            [ledger],
            club_slug="aces-table",
        ).rows
        self.assertEqual(rows[0].match_name, "Miah Xeshan")
        self.assertEqual(rows[0].match_source, "Zelle")
        self.assertEqual(rows[0].variant, "gto zelle")
        self.assertFalse(rows[0].vaughn_method)

    def test_different_player_ids_not_matched(self):
        """HunnidPrblms must not consume Tiddlemouse19's Stripe (Aug 15)."""
        hunnid = _trade(
            line_id=1,
            gg_id="1055-4566",
            nick="HunnidPrblms",
            occurred=self.t0,
            amount="-50",
        )
        tiddle = _trade(
            line_id=2,
            gg_id="8196-3440",
            nick="Tiddlemouse19",
            occurred=self.t0 + timedelta(minutes=2),
            amount="-50",
            sheet_row=2,
        )
        stripe = _ledger(
            gg_id="8196-3440",
            nick="Tiddlemouse19",
            occurred=self.t0 + timedelta(minutes=2),
            amount_signed="-50",
            display_name="Tiddlemouse19",
            external_id="deposit_stripe:tiddle",
        )
        result = match_trade_lines_to_ledger(
            [hunnid, tiddle],
            [stripe],
            club_slug="round-table",
        )
        self.assertEqual(result.rows[0].match_source, "")
        self.assertEqual(result.rows[1].match_source, "Stripe")
        self.assertEqual(result.rows[1].match_name, "Tiddlemouse19")
        self.assertEqual(result.unmatched_ledger, [])

    def test_wrong_player_stripe_does_not_steal_zelle_or_block_rt_at(self):
        """Aug 15 cascade: Stripe→Hunnid, Zelle→Tiddlemouse, Tonka unmatched."""
        hunnid_at = _trade(
            line_id=1,
            gg_id="1055-4566",
            nick="HunnidPrblms",
            occurred=self.t0,
            amount="50",
            club="aces-table",
        )
        hunnid_rt = _trade(
            line_id=2,
            gg_id="1055-4566",
            nick="HunnidPrblms",
            occurred=self.t0 + timedelta(seconds=8),
            amount="-50",
            club="round-table",
            sheet_row=2,
        )
        tiddle = _trade(
            line_id=3,
            gg_id="8196-3440",
            nick="Tiddlemouse19",
            occurred=self.t0 + timedelta(minutes=2),
            amount="-50",
            club="round-table",
            sheet_row=3,
        )
        tonka = _trade(
            line_id=4,
            gg_id="8064-5209",
            nick="Tonkatrucktaha",
            occurred=self.t0 + timedelta(minutes=8),
            amount="-50",
            club="round-table",
            sheet_row=4,
        )
        stripe = _ledger(
            gg_id="8196-3440",
            nick="Tiddlemouse19",
            occurred=self.t0 + timedelta(minutes=2),
            amount_signed="-50",
            display_name="Tiddlemouse19",
            external_id="deposit_stripe:tiddle",
        )
        zelle = _ledger(
            gg_id="8064-5209",
            nick="Tonkatrucktaha",
            occurred=self.t0 + timedelta(minutes=7),
            amount_signed="-50",
            source="deposit_zelle",
            source_label="Zelle",
            display_name="Ethan Tucker",
            external_id="deposit_zelle:tonka",
            variant="coachingg444@gmail.com",
        )
        gra8 = _trade(
            line_id=5,
            gg_id="5181-0004",
            nick="GRA8",
            occurred=self.t0 + timedelta(hours=1),
            amount="-100",
            club="aces-table",
            sheet_row=5,
        )
        mook_stripe = _ledger(
            gg_id="1680-2327",
            nick="mookboy",
            occurred=self.t0 + timedelta(hours=1, minutes=10),
            amount_signed="-100",
            display_name="mookboy",
            external_id="deposit_stripe:mook",
        )
        result = match_trade_lines_to_ledger(
            [hunnid_at, hunnid_rt, tiddle, tonka, gra8],
            [stripe, zelle, mook_stripe],
            club_slug="round-table",
        )
        rows = apply_chip_transfer_matches(result.rows)
        by_id = {row.trade.line_id: row for row in rows}
        self.assertEqual(by_id[1].match_source, CHIP_TRANSFER_RT_AT_LABEL)
        self.assertEqual(by_id[2].match_source, CHIP_TRANSFER_RT_AT_LABEL)
        self.assertEqual(by_id[3].match_source, "Stripe")
        self.assertEqual(by_id[3].match_name, "Tiddlemouse19")
        self.assertEqual(by_id[4].match_source, "Zelle")
        self.assertEqual(by_id[4].match_name, "Ethan Tucker")
        self.assertEqual(by_id[5].match_source, "")
        self.assertEqual(
            [line.external_id for line in result.unmatched_ledger],
            ["deposit_stripe:mook"],
        )

    def test_vaughn_zelle_flag(self):
        trade = _trade(occurred=self.t0, amount="-50")
        ledger = _ledger(
            occurred=self.t0,
            amount_signed="-50",
            source="deposit_zelle",
            source_label="Zelle",
            external_id="deposit_zelle:vaughn",
            display_name="Payer",
            variant="2133729202",
        )
        rows = match_trade_lines_to_ledger(
            [trade],
            [ledger],
            club_slug="clubgto",
        ).rows
        self.assertTrue(rows[0].vaughn_method)
        self.assertEqual(rows[0].match_source, "GTO Zelle")

    def test_rt_zelle_source_on_clubgto(self):
        trade = _trade(occurred=self.t0, amount="-50")
        ledger = _ledger(
            occurred=self.t0,
            amount_signed="-50",
            source="deposit_zelle",
            source_label="Zelle",
            external_id="deposit_zelle:rt",
            display_name="Payer",
            variant="coachingg444@gmail.com",
        )
        rows = match_trade_lines_to_ledger(
            [trade],
            [ledger],
            club_slug="clubgto",
        ).rows
        self.assertFalse(rows[0].vaughn_method)
        self.assertEqual(rows[0].match_source, "RT Zelle")

    def test_vaughn_crypto_clubgto(self):
        trade = _trade(occurred=self.t0, amount="-30")
        ledger = _ledger(
            occurred=self.t0,
            amount_signed="-30",
            source="deposit_crypto",
            source_label="Crypto",
            external_id="deposit_crypto:1",
            display_name="Wallet",
            variant="USDT",
        )
        rows = match_trade_lines_to_ledger(
            [trade],
            [ledger],
            club_slug="clubgto",
        ).rows
        self.assertTrue(rows[0].vaughn_method)
        self.assertEqual(rows[0].match_source, "GTO Crypto")
        rows_rt = match_trade_lines_to_ledger(
            [trade],
            [ledger],
            club_slug="round-table",
        ).rows
        self.assertFalse(rows_rt[0].vaughn_method)
        self.assertEqual(rows_rt[0].match_source, "Crypto")

    def test_vaughn_stripe_clubgto(self):
        trade = _trade(occurred=self.t0, amount="-40")
        ledger = _ledger(
            occurred=self.t0,
            amount_signed="-40",
            source="deposit_stripe",
            source_label="Stripe",
            external_id="deposit_stripe:1",
            display_name="Card",
            variant=None,
        )
        rows = match_trade_lines_to_ledger(
            [trade],
            [ledger],
            club_slug="clubgto",
        ).rows
        self.assertTrue(rows[0].vaughn_method)
        self.assertEqual(rows[0].match_source, "GTO Stripe")
        rows_rt = match_trade_lines_to_ledger(
            [trade],
            [ledger],
            club_slug="round-table",
        ).rows
        self.assertFalse(rows_rt[0].vaughn_method)
        self.assertEqual(rows_rt[0].match_source, "Stripe")

    def test_sign_mismatch_rejected(self):
        trade = _trade(occurred=self.t0, amount="-100")
        ledger = _ledger(
            occurred=self.t0,
            amount_signed="100",
            source="cashout",
            source_label="Cashout",
            external_id="cashout:1",
        )
        rows = match_trade_lines_to_ledger(
            [trade],
            [ledger],
            club_slug="aces-table",
        ).rows
        self.assertEqual(rows[0].match_name, "")
        self.assertEqual(rows[0].match_amount, None)

    def test_outside_window_blank(self):
        trade = _trade(occurred=self.t0, amount="-100")
        ledger = _ledger(
            occurred=self.t0 + timedelta(minutes=16),
            amount_signed="-100",
        )
        rows = match_trade_lines_to_ledger(
            [trade],
            [ledger],
            club_slug="aces-table",
        ).rows
        self.assertEqual(rows[0].match_name, "")

    def test_rounding_half_up_matches(self):
        trade = _trade(occurred=self.t0, amount="-99.50")
        ledger = _ledger(occurred=self.t0, amount_signed="-100")
        rows = match_trade_lines_to_ledger(
            [trade],
            [ledger],
            club_slug="aces-table",
        ).rows
        self.assertEqual(rows[0].match_amount, Decimal("100"))

    def test_early_rb_fractional_matches_floored_trade(self):
        """Early RB $18.60 (rounds to 19) ↔ ClubGG chips $18 within ±$1."""
        trade = _trade(occurred=self.t0, amount="-18")
        ledger = _ledger(
            occurred=self.t0,
            amount_signed="-18.60",
            source="early_rakeback",
            source_label="Early RB",
            external_id="early_rakeback:1",
            display_name="RB Player",
        )
        rows = match_trade_lines_to_ledger(
            [trade],
            [ledger],
            club_slug="aces-table",
        ).rows
        self.assertEqual(rows[0].match_source, "Early RB")
        self.assertEqual(rows[0].match_name, "RB Player")
        self.assertEqual(rows[0].match_amount, Decimal("19"))

    def test_amount_delta_over_one_dollar_rejected(self):
        trade = _trade(occurred=self.t0, amount="-18")
        ledger = _ledger(
            occurred=self.t0,
            amount_signed="-20.00",
            source="early_rakeback",
            source_label="Early RB",
            external_id="early_rakeback:2",
        )
        rows = match_trade_lines_to_ledger(
            [trade],
            [ledger],
            club_slug="aces-table",
        ).rows
        self.assertEqual(rows[0].match_name, "")
        self.assertIsNone(rows[0].match_amount)

    def test_prefers_exact_amount_over_nearby(self):
        trade = _trade(occurred=self.t0, amount="-18")
        near = _ledger(
            occurred=self.t0,
            amount_signed="-18.60",
            source="early_rakeback",
            source_label="Early RB",
            external_id="early_rakeback:near",
            display_name="Near",
        )
        exact = _ledger(
            occurred=self.t0 + timedelta(seconds=30),
            amount_signed="-18",
            source="early_rakeback",
            source_label="Early RB",
            external_id="early_rakeback:exact",
            display_name="Exact",
        )
        rows = match_trade_lines_to_ledger(
            [trade],
            [near, exact],
            club_slug="aces-table",
        ).rows
        self.assertEqual(rows[0].match_name, "Exact")
        self.assertEqual(rows[0].match_amount, Decimal("18"))

    def test_bonus_fills_variant(self):
        trade = _trade(occurred=self.t0, amount="-25")
        ledger = _ledger(
            occurred=self.t0,
            amount_signed="-25",
            source="bonus",
            source_label="Bonus",
            external_id="bonus:1",
            display_name="Bonus Player",
            variant="Welcome — first deposit",
        )
        rows = match_trade_lines_to_ledger(
            [trade],
            [ledger],
            club_slug="aces-table",
        ).rows
        self.assertEqual(rows[0].match_source, "Bonus")
        self.assertEqual(rows[0].variant, "Welcome — first deposit")

    def test_zelle_tag_fills_variant(self):
        trade = _trade(occurred=self.t0, amount="-20")
        ledger = _ledger(
            occurred=self.t0,
            amount_signed="-20",
            source="deposit_zelle",
            source_label="Zelle",
            external_id="deposit_zelle:9",
            display_name="Payer",
            variant="gto-zelle-inbox",
        )
        rows = match_trade_lines_to_ledger(
            [trade],
            [ledger],
            club_slug="aces-table",
        ).rows
        self.assertEqual(rows[0].variant, "gto-zelle-inbox")

    def test_stripe_variant_blank(self):
        trade = _trade(occurred=self.t0, amount="-20")
        ledger = _ledger(
            occurred=self.t0,
            amount_signed="-20",
            display_name="Stripe Player",
            variant=None,
        )
        rows = match_trade_lines_to_ledger(
            [trade],
            [ledger],
            club_slug="aces-table",
        ).rows
        self.assertEqual(rows[0].variant, "")

    def test_cashout_method_label_variant_blank(self):
        trade = _trade(occurred=self.t0, amount="100")
        ledger = _ledger(
            occurred=self.t0,
            amount_signed="100",
            source="cashout",
            source_label="Cashout Venmo",
            external_id="cashout:1",
            variant=None,
        )
        rows = match_trade_lines_to_ledger(
            [trade],
            [ledger],
            club_slug="clubgto",
        ).rows
        self.assertEqual(rows[0].match_source, "Cashout Venmo")
        self.assertEqual(rows[0].variant, "")


def _with_transfers(trades, ledgers, *, club_slug: str = "round-table"):
    result = match_trade_lines_to_ledger(trades, ledgers, club_slug=club_slug)
    return apply_chip_transfer_matches(result.rows)


class ChipTransferMatchTestCase(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 7, 3, 6, 30, tzinfo=timezone.utc)

    def test_inter_player_pair_after_unmatched_ledger(self):
        add = _trade(
            line_id=1,
            amount="-100",
            gg_id="1111-1111",
            nick="Alice",
            occurred=self.t0,
            club="clubgto",
        )
        claim = _trade(
            line_id=2,
            amount="100",
            gg_id="2222-2222",
            nick="Bob",
            occurred=self.t0 + timedelta(minutes=4),
            club="clubgto",
        )
        rows = _with_transfers([add, claim], [], club_slug="clubgto")
        self.assertEqual(rows[0].match_source, CHIP_TRANSFER_PLAYER_LABEL)
        self.assertEqual(rows[1].match_source, CHIP_TRANSFER_PLAYER_LABEL)
        self.assertEqual(rows[0].match_name, "Bob")
        self.assertEqual(rows[1].match_name, "Alice")
        self.assertEqual(rows[0].match_amount, Decimal("100"))
        self.assertEqual(rows[1].variant, "")
        self.assertEqual(rows[0].match_occurred_at, claim.occurred_at)

    def test_ledger_match_wins_over_nearby_opposite_trade(self):
        add = _trade(
            line_id=1,
            amount="-100",
            gg_id="1111-1111",
            nick="Alice",
            occurred=self.t0,
        )
        claim = _trade(
            line_id=2,
            amount="100",
            gg_id="2222-2222",
            nick="Bob",
            occurred=self.t0,
        )
        ledger = _ledger(
            occurred=self.t0,
            amount_signed="-100",
            gg_id="1111-1111",
            nick="Alice",
            source_label="Stripe",
        )
        rows = _with_transfers([add, claim], [ledger])
        self.assertEqual(rows[0].match_source, "Stripe")
        self.assertEqual(rows[1].match_source, "")

    def test_inter_player_preferred_over_rt_at(self):
        rt_a = _trade(
            line_id=1,
            amount="-100",
            gg_id="1111-1111",
            nick="Alice",
            occurred=self.t0,
            club="round-table",
        )
        rt_b = _trade(
            line_id=2,
            amount="100",
            gg_id="2222-2222",
            nick="Bob",
            occurred=self.t0,
            club="round-table",
        )
        at_a = _trade(
            line_id=3,
            amount="100",
            gg_id="1111-1111",
            nick="Alice",
            occurred=self.t0,
            club="aces-table",
        )
        rows = _with_transfers([rt_a, rt_b, at_a], [])
        by_id = {row.trade.line_id: row for row in rows}
        self.assertEqual(by_id[1].match_source, CHIP_TRANSFER_PLAYER_LABEL)
        self.assertEqual(by_id[2].match_source, CHIP_TRANSFER_PLAYER_LABEL)
        self.assertEqual(by_id[3].match_source, "")

    def test_rt_at_same_player_different_clubs(self):
        rt = _trade(
            line_id=1,
            amount="-80",
            gg_id="1111-1111",
            nick="Alice",
            occurred=self.t0,
            club="round-table",
        )
        at = _trade(
            line_id=2,
            amount="80",
            gg_id="1111-1111",
            nick="Alice",
            occurred=self.t0 + timedelta(minutes=10),
            club="aces-table",
        )
        rows = _with_transfers([rt, at], [])
        self.assertEqual(rows[0].match_source, CHIP_TRANSFER_RT_AT_LABEL)
        self.assertEqual(rows[1].match_source, CHIP_TRANSFER_RT_AT_LABEL)
        self.assertEqual(rows[0].match_name, "Aces Table")
        self.assertEqual(rows[1].match_name, "Round Table")

    def test_at_cc_same_player_different_clubs(self):
        at = _trade(
            line_id=1,
            amount="-80",
            gg_id="1111-1111",
            nick="Alice",
            occurred=self.t0,
            club="aces-table",
        )
        cc = _trade(
            line_id=2,
            amount="80",
            gg_id="1111-1111",
            nick="Alice",
            occurred=self.t0 + timedelta(minutes=10),
            club="creator-club",
        )
        rows = _with_transfers([at, cc], [])
        self.assertEqual(rows[0].match_source, CHIP_TRANSFER_AT_CC_LABEL)
        self.assertEqual(rows[1].match_source, CHIP_TRANSFER_AT_CC_LABEL)
        self.assertEqual(rows[0].match_name, "Creator Club")
        self.assertEqual(rows[1].match_name, "Aces Table")

    def test_rt_at_preferred_over_at_cc(self):
        rt = _trade(
            line_id=1,
            amount="-80",
            gg_id="1111-1111",
            nick="Alice",
            occurred=self.t0,
            club="round-table",
        )
        at = _trade(
            line_id=2,
            amount="80",
            gg_id="1111-1111",
            nick="Alice",
            occurred=self.t0,
            club="aces-table",
        )
        cc = _trade(
            line_id=3,
            amount="-80",
            gg_id="1111-1111",
            nick="Alice",
            occurred=self.t0,
            club="creator-club",
        )
        rows = _with_transfers([rt, at, cc], [])
        by_id = {row.trade.line_id: row for row in rows}
        self.assertEqual(by_id[1].match_source, CHIP_TRANSFER_RT_AT_LABEL)
        self.assertEqual(by_id[2].match_source, CHIP_TRANSFER_RT_AT_LABEL)
        self.assertEqual(by_id[3].match_source, "")

    def test_amount_mismatch_rejected(self):
        add = _trade(line_id=1, amount="-100", gg_id="1111-1111", occurred=self.t0)
        claim = _trade(
            line_id=2,
            amount="99",
            gg_id="2222-2222",
            occurred=self.t0,
        )
        rows = _with_transfers([add, claim], [])
        self.assertEqual(rows[0].match_source, "")
        self.assertEqual(rows[1].match_source, "")

    def test_window_eleven_minutes_rejected(self):
        add = _trade(line_id=1, amount="-100", gg_id="1111-1111", occurred=self.t0)
        claim = _trade(
            line_id=2,
            amount="100",
            gg_id="2222-2222",
            occurred=self.t0 + timedelta(minutes=11),
        )
        rows = _with_transfers([add, claim], [])
        self.assertEqual(rows[0].match_source, "")

    def test_missing_player_id_skipped(self):
        add = _trade(line_id=1, amount="-100", gg_id=None, occurred=self.t0)
        claim = _trade(
            line_id=2,
            amount="100",
            gg_id="2222-2222",
            occurred=self.t0,
        )
        rows = _with_transfers([add, claim], [])
        self.assertEqual(rows[0].match_source, "")
        self.assertEqual(rows[1].match_source, "")


if __name__ == "__main__":
    unittest.main()
