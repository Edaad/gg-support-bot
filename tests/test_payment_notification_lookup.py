"""Tests for payment notification message lookup (reply-to-bind)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from notification.handlers.bind import _reply_targets_payment_notification
from notification.payment_lookup import PaymentRef, find_payment_by_notification


class PaymentNotificationLookupTestCase(unittest.TestCase):
    @patch("notification.payment_lookup.get_db")
    def test_find_payment_matches_chat_id_variants(self, mock_get_db):
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session

        payment = SimpleNamespace(
            id=386,
            is_test=True,
            telegram_chat_id=None,
        )
        session.query.return_value.filter.return_value.one_or_none.side_effect = [
            None,
            None,
            None,
            payment,
        ]

        ref = find_payment_by_notification(-5273879167, 8891)

        self.assertIsNotNone(ref)
        self.assertEqual(ref.method_slug, "zelle")
        self.assertEqual(ref.payment_id, 386)
        filter_call = session.query.return_value.filter.call_args
        chat_ids = filter_call[0][0].right.value
        self.assertIn(-5273879167, chat_ids)
        self.assertIn(-1005273879167, chat_ids)

    @patch("notification.handlers.bind.find_payment_by_notification", return_value=None)
    def test_reply_targets_requires_payment_notification_text_when_not_in_db(
        self,
        _mock_find,
    ):
        reply = SimpleNamespace(message_id=1, text="Some other bot message")
        self.assertFalse(
            _reply_targets_payment_notification(
                reply,
                notification_chat_id=-5273879167,
            )
        )

    @patch(
        "notification.handlers.bind.find_payment_by_notification",
        return_value=PaymentRef(
            method_slug="zelle",
            payment_id=386,
            payment_is_test=True,
            telegram_chat_id=None,
        ),
    )
    def test_reply_targets_accepts_db_match_without_reply_text(self, _mock_find):
        reply = SimpleNamespace(message_id=8891, text=None)
        self.assertTrue(
            _reply_targets_payment_notification(
                reply,
                notification_chat_id=-5273879167,
            )
        )


if __name__ == "__main__":
    unittest.main()
