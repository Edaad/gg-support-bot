"""Club applepay/debitcard are hidden from /deposit; union applepay stays."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from bot.services.club_payment_v2 import (
    _HIDDEN_CLUB_DEPOSIT_SLUGS,
    is_hidden_club_deposit_method,
)


class HiddenClubDepositMethodsTestCase(unittest.TestCase):
    def test_hidden_slugs(self):
        self.assertEqual(_HIDDEN_CLUB_DEPOSIT_SLUGS, frozenset({"applepay", "debitcard"}))

    def test_club_applepay_and_debitcard_hidden(self):
        self.assertTrue(
            is_hidden_club_deposit_method(
                {"slug": "applepay", "tracks_manual_requests": False}
            )
        )
        self.assertTrue(
            is_hidden_club_deposit_method(
                {"slug": "debitcard", "tracks_manual_requests": False}
            )
        )
        self.assertTrue(
            is_hidden_club_deposit_method(
                SimpleNamespace(slug="ApplePay", tracks_manual_requests=False)
            )
        )

    def test_union_applepay_not_hidden(self):
        self.assertFalse(
            is_hidden_club_deposit_method(
                {"slug": "applepay", "tracks_manual_requests": True}
            )
        )
        self.assertFalse(
            is_hidden_club_deposit_method(
                SimpleNamespace(slug="applepay", tracks_manual_requests=True)
            )
        )

    def test_cashapp_and_others_not_hidden(self):
        self.assertFalse(
            is_hidden_club_deposit_method(
                {"slug": "cashapp", "tracks_manual_requests": False}
            )
        )
        self.assertFalse(
            is_hidden_club_deposit_method(
                {"slug": "venmo", "tracks_manual_requests": False}
            )
        )


if __name__ == "__main__":
    unittest.main()
