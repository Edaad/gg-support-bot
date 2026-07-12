"""Tests for best-effort trade ↔ ledger matching."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from api.audit_ledger import LedgerLine
from api.audit_reconcile import TradeLineForMatch
from api.audit_reconcile_matching import (
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
) -> TradeLineForMatch:
    return TradeLineForMatch(
        line_id=line_id,
        occurred_at=occurred,
        amount=Decimal(amount),
        member_gg_player_id=gg_id,
        member_nickname=nick,
        sheet_row=sheet_row,
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
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].match_name, "Jane Doe")
        self.assertEqual(rows[0].match_source, "Stripe")
        self.assertEqual(rows[0].match_amount, "$100")
        self.assertEqual(rows[0].variant, "")

        trade2 = _trade(line_id=2, occurred=self.t0, amount="-100", sheet_row=2)
        rows2 = match_trade_lines_to_ledger(
            [trade, trade2],
            [ledger],
            club_slug="aces-table",
        )
        self.assertEqual(rows2[0].match_name, "Jane Doe")
        self.assertEqual(rows2[1].match_name, "")
        self.assertEqual(rows2[1].match_source, "")

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
        )
        self.assertEqual(rows[0].match_name, "Miah Xeshan")
        self.assertEqual(rows[0].match_source, "Zelle")
        self.assertEqual(rows[0].variant, "gto zelle")

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
        )
        self.assertEqual(rows[0].match_name, "")
        self.assertEqual(rows[0].match_amount, "")

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
        )
        self.assertEqual(rows[0].match_name, "")

    def test_rounding_half_up_matches(self):
        trade = _trade(occurred=self.t0, amount="-99.50")
        ledger = _ledger(occurred=self.t0, amount_signed="-100")
        rows = match_trade_lines_to_ledger(
            [trade],
            [ledger],
            club_slug="aces-table",
        )
        self.assertEqual(rows[0].match_amount, "$100")

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
        )
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
        )
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
        )
        self.assertEqual(rows[0].variant, "")


if __name__ == "__main__":
    unittest.main()
