"""Slack reminder copy and schedule for still-unbound payments."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bot.services.unbound_payment_reminders import (
    format_unbound_payment_reminder,
    reminder_is_due,
    waiting_minutes,
)
from db.models import CryptoPayment, VenmoPayment

NOW = datetime(2026, 10, 23, 1, 20, tzinfo=timezone.utc)


class ReminderScheduleTestCase(unittest.TestCase):
    def test_not_due_before_five_minutes(self):
        created = NOW - timedelta(minutes=4, seconds=59)
        self.assertFalse(
            reminder_is_due(created_at=created, last_reminded_at=None, now=NOW)
        )

    def test_first_ping_at_five_minutes(self):
        created = NOW - timedelta(minutes=5)
        self.assertTrue(
            reminder_is_due(created_at=created, last_reminded_at=None, now=NOW)
        )

    def test_repeat_waits_another_five_minutes(self):
        created = NOW - timedelta(minutes=9)
        last = NOW - timedelta(minutes=4)
        self.assertFalse(
            reminder_is_due(created_at=created, last_reminded_at=last, now=NOW)
        )
        last = NOW - timedelta(minutes=5)
        self.assertTrue(
            reminder_is_due(created_at=created, last_reminded_at=last, now=NOW)
        )

    def test_waiting_minutes_follow_elapsed_time(self):
        created = NOW - timedelta(minutes=5)
        self.assertEqual(waiting_minutes(created, NOW), 5)
        created = NOW - timedelta(minutes=10, seconds=30)
        self.assertEqual(waiting_minutes(created, NOW), 10)


class ReminderFormatTestCase(unittest.TestCase):
    def test_exchange_crypto_reminder(self):
        payment = CryptoPayment(
            amount_cents=50000,
            token_symbol="USDC",
            chain="eth",
            from_address="0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43",
            from_entity_name="Coinbase",
            to_address="0xdef",
            transaction_hash="0xabc123def456",
            paid_at="2026-10-23T01:15:00Z",
            alert_name="RT/AT/CC Crypto Payment",
            alert_scope="rt_at_cc",
        )
        text = format_unbound_payment_reminder(
            payment,
            method_slug="crypto",
            waiting_minutes_value=5,
            candidate_titles=["RT / 7657-1318 / Yoter Cat"],
        )
        self.assertEqual(
            text,
            "\n".join(
                [
                    "*5 minutes have passed and this payment is still unbound — match it to a group.*",
                    "",
                    "Method: Crypto",
                    "Alert: RT/AT/CC Crypto Payment",
                    "Amount: $500",
                    "Chain: ETH",
                    "From: Coinbase (0xa9d1…3e43)",
                    "Tx: 0xabc123def456",
                    "Paid: Oct 22, 2026 09:15 PM EST",
                ]
            ),
        )

    def test_venmo_reminder_lists_candidate_groups(self):
        payment = VenmoPayment(
            amount_cents=20000,
            payer_name="Jane Doe",
            venmo_handle="roundtable",
            memo="poker",
        )
        text = format_unbound_payment_reminder(
            payment,
            method_slug="venmo",
            waiting_minutes_value=10,
            candidate_titles=[
                "RT / 7657-1318 / Yoter Cat",
                "CC / 3715-7523 / freeimp",
            ],
        )
        self.assertEqual(
            text,
            "\n".join(
                [
                    "*10 minutes have passed and this payment is still unbound — match it to a group.*",
                    "",
                    "Method: Venmo",
                    "Name: Jane Doe",
                    "Amount: $200",
                    "Memo: poker",
                    "Handle: @roundtable",
                    "",
                    "Possible groups:",
                    "`RT / 7657-1318 / Yoter Cat`",
                    "`CC / 3715-7523 / freeimp`",
                ]
            ),
        )

    def test_personal_crypto_includes_candidate_groups(self):
        payment = SimpleNamespace(
            amount_cents=10000,
            chain="btc",
            from_address="bc1qexampleaddress",
            from_entity_name=None,
            transaction_hash="abc",
            paid_at=None,
            alert_name="ClubGTO Crypto Payment",
        )
        text = format_unbound_payment_reminder(
            payment,
            method_slug="crypto",
            waiting_minutes_value=5,
            candidate_titles=["GTO / 2760-4009 / Capitanpolo"],
        )
        self.assertIn("Possible groups:", text)
        self.assertIn("`GTO / 2760-4009 / Capitanpolo`", text)
        self.assertNotIn("Paid:", text)


if __name__ == "__main__":
    unittest.main()
