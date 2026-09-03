"""Tests for Venmo/Zelle refund gates (G&S, fractional amount, banned memo)."""

from __future__ import annotations

import unittest

from types import SimpleNamespace

from bot.services.payment_refund_gate import (
    REASON_BANNED_MEMO,
    REASON_GOODS_SERVICES,
    append_whole_dollar_nudge,
    evaluate_refund_gate,
    find_banned_memo_hits,
    format_player_refund_message,
    format_refund_issue_report_description,
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

    def test_fractional_amount_does_not_block(self):
        repeat = evaluate_refund_gate(amount_cents=5025, method_slug="venmo")
        self.assertFalse(repeat.requires_refund)
        self.assertTrue(repeat.warn_whole_dollar)
        first = evaluate_refund_gate(
            amount_cents=8999,
            is_first_time_setup_bind=True,
            method_slug="zelle",
        )
        self.assertFalse(first.requires_refund)
        self.assertFalse(first.warn_whole_dollar)

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


class RefundIssueReportDescriptionTests(unittest.TestCase):
    def test_banned_memo_omits_ids_and_bolds_hits(self):
        payment = SimpleNamespace(
            id=2614,
            payer_name="Drew berry",
            amount_cents=4999,
            venmo_handle="@michaelc4444",
            memo="Chips",
        )
        gate = evaluate_refund_gate(
            amount_cents=4999, memo="Chips", method_slug="venmo"
        )
        text = format_refund_issue_report_description(
            payment,
            gate,
            method_slug="venmo",
            group_title="RT / 1758-7219 / drubby459",
            notification_chat_id=-5549765036,
            notification_message_id=37711,
        )
        self.assertIn("Memo contains banned keyword(s): *chips*.", text)
        self.assertNotIn("Payment ID:", text)
        self.assertNotIn("Staff notification:", text)
        self.assertIn("Payer: Drew berry", text)
        self.assertIn("Amount: $49.99", text)
        self.assertIn("Memo: Chips", text)


class RefundCopyTests(unittest.TestCase):
    def test_injects_staff_banner(self):
        gate = evaluate_refund_gate(amount_cents=5025, memo="poker")
        text = inject_refund_banner("🔔 Venmo Payment Notification\nGroup Chat: Unbound", gate)
        self.assertIn("DO NOT ADD", text)
        self.assertNotIn("Non-whole-dollar amount", text)
        self.assertIn("Banned memo (poker)", text)

    def test_cents_only_has_no_staff_block(self):
        gate = evaluate_refund_gate(amount_cents=5025, method_slug="zelle")
        text = inject_refund_banner("🔔 Zelle Payment Notification\nGroup Chat: Unbound", gate)
        self.assertNotIn("DO NOT ADD", text)
        self.assertTrue(gate.warn_whole_dollar)

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
            amount_cents=5000, memo="poker", method_slug="zelle"
        )
        text = format_player_refund_message(5000, gate, method_slug="zelle")
        self.assertNotIn("Friends & Family", text)
        self.assertIn("memo", text)
        self.assertNotIn("whole-dollar", text)

    def test_appends_whole_dollar_nudge(self):
        gate = evaluate_refund_gate(amount_cents=5025, method_slug="zelle")
        text = append_whole_dollar_nudge(
            "We have received your payment for $50, credits will be loaded to your account shortly!!",
            gate,
        )
        self.assertIn("whole-dollar amounts from now on", text)
        first = evaluate_refund_gate(
            amount_cents=5025,
            is_first_time_setup_bind=True,
            method_slug="zelle",
        )
        unchanged = "We have received your payment for $50, credits will be loaded to your account shortly!!"
        self.assertEqual(append_whole_dollar_nudge(unchanged, first), unchanged)


class SpecialAmountSetupLookupTests(unittest.TestCase):
    def test_pending_special_amount_skips_whole_dollar_warn(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        from bot.services.payment_refund_gate import refund_gate_for_payment

        payment = SimpleNamespace(
            id=99,
            amount_cents=8999,
            memo=None,
            goods_or_services=False,
        )
        with patch(
            "bot.services.payment_refund_gate.is_special_amount_setup",
            return_value=True,
        ):
            gate = refund_gate_for_payment("zelle", payment)
        self.assertFalse(gate.requires_refund)
        self.assertFalse(gate.warn_whole_dollar)

    def test_repeat_cents_without_setup_warns_only(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        from bot.services.payment_refund_gate import refund_gate_for_payment

        payment = SimpleNamespace(
            id=100,
            amount_cents=5025,
            memo=None,
            goods_or_services=False,
        )
        with patch(
            "bot.services.payment_refund_gate.is_special_amount_setup",
            return_value=False,
        ):
            gate = refund_gate_for_payment("zelle", payment)
        self.assertFalse(gate.requires_refund)
        self.assertTrue(gate.warn_whole_dollar)


if __name__ == "__main__":
    unittest.main()
