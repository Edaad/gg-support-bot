"""Tests for Venmo/Zelle refund gates (G&S, fractional amount, banned memo)."""

from __future__ import annotations

import unittest

from bot.services.payment_refund_gate import (
    REASON_BANNED_MEMO,
    REASON_FRACTIONAL_AMOUNT,
    REASON_GOODS_SERVICES,
    evaluate_refund_gate,
    find_banned_memo_hits,
    format_player_refund_message,
    inject_refund_banner,
)


class BannedMemoTests(unittest.TestCase):
    def test_matches_listed_keywords(self):
        self.assertIn("poker", find_banned_memo_hits("for poker tonight"))
        self.assertIn("GG", find_banned_memo_hits("thanks GG"))
        self.assertIn("RT", find_banned_memo_hits("RT deposit"))
        self.assertIn("Club GG", find_banned_memo_hits("ClubGG"))
        self.assertIn("Round Table", find_banned_memo_hits("round table"))
        self.assertIn("buy-in", find_banned_memo_hits("buy in"))
        self.assertIn("chips", find_banned_memo_hits("load chips"))

    def test_ignores_short_token_substrings(self):
        self.assertEqual(find_banned_memo_hits("bright morning"), ())
        self.assertEqual(find_banned_memo_hits("bigger"), ())
        self.assertEqual(find_banned_memo_hits("dinner"), ())


class EvaluateRefundGateTests(unittest.TestCase):
    def test_whole_dollar_clean_memo_is_clear(self):
        gate = evaluate_refund_gate(amount_cents=5000, memo="thanks")
        self.assertFalse(gate.requires_refund)

    def test_goods_and_services_venmo_only(self):
        venmo = evaluate_refund_gate(
            amount_cents=8000, goods_or_services=True, method_slug="venmo"
        )
        self.assertEqual(venmo.reasons, (REASON_GOODS_SERVICES,))
        zelle = evaluate_refund_gate(
            amount_cents=8000, goods_or_services=True, method_slug="zelle"
        )
        self.assertFalse(zelle.requires_refund)

    def test_fractional_refunds_unless_first_time_setup(self):
        repeat = evaluate_refund_gate(amount_cents=5025, method_slug="venmo")
        self.assertIn(REASON_FRACTIONAL_AMOUNT, repeat.reasons)
        first = evaluate_refund_gate(
            amount_cents=8999,
            is_first_time_setup_bind=True,
            method_slug="zelle",
        )
        self.assertFalse(first.requires_refund)

    def test_banned_memo_refunds_even_on_first_time(self):
        gate = evaluate_refund_gate(
            amount_cents=8999,
            memo="poker",
            is_first_time_setup_bind=True,
            method_slug="zelle",
        )
        self.assertEqual(gate.reasons, (REASON_BANNED_MEMO,))

    def test_skips_other_methods(self):
        gate = evaluate_refund_gate(
            amount_cents=5025,
            memo="poker",
            method_slug="cashapp",
        )
        self.assertFalse(gate.requires_refund)


class RefundCopyTests(unittest.TestCase):
    def test_injects_staff_banner(self):
        gate = evaluate_refund_gate(amount_cents=5025, memo="poker")
        text = inject_refund_banner("🔔 Venmo Payment Notification\nGroup Chat: Unbound", gate)
        self.assertIn("DO NOT ADD", text)
        self.assertIn("Non-whole-dollar amount", text)
        self.assertIn("Banned memo (poker)", text)

    def test_player_gs_copy_unchanged(self):
        gate = evaluate_refund_gate(amount_cents=8000, goods_or_services=True)
        self.assertEqual(
            format_player_refund_message(8000, gate, method_slug="venmo"),
            "We have received your payment for $80. "
            "Since it was sent as Goods & Services, we will refund it — "
            "please resend as Friends & Family.",
        )

    def test_zelle_player_copy_omits_friends_and_family(self):
        gate = evaluate_refund_gate(
            amount_cents=5025, memo="poker", method_slug="zelle"
        )
        text = format_player_refund_message(5025, gate, method_slug="zelle")
        self.assertNotIn("Friends & Family", text)
        self.assertIn("whole-dollar", text)
        self.assertIn("memo", text)


if __name__ == "__main__":
    unittest.main()
