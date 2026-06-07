"""Unit tests for Zelle payment notification formatting."""

import unittest

from bot.services.zelle_payments import format_notification_text
from db.models import ZellePayment


class TestZelleNotificationFormat(unittest.TestCase):
    def test_unbound_notification(self):
        payment = ZellePayment(
            id=1,
            payer_name="Jane Doe",
            amount_cents=20000,
            zelle_recipient="pay@example.com",
        )
        text = format_notification_text(payment)
        self.assertIn("Zelle Payment Notification", text)
        self.assertIn("Unbound", text)
        self.assertIn("Jane Doe", text)
        self.assertIn("pay@example.com", text)
        self.assertIn("<b>$200</b>", text)
        self.assertNotIn("Goods/Services", text)
        self.assertNotIn("Open group chat", text)

    def test_bound_notification(self):
        payment = ZellePayment(
            id=2,
            payer_name="Jane Doe",
            amount_cents=10000,
            zelle_recipient="3105670961",
            telegram_chat_id=-1001234567890,
        )
        text = format_notification_text(payment, group_title="RT / 1234 / Player")
        self.assertIn("RT / 1234 / Player", text)
        self.assertNotIn("Unbound", text)
        self.assertIn('href="https://t.me/c/1234567890">Open group chat</a>', text)


if __name__ == "__main__":
    unittest.main()
