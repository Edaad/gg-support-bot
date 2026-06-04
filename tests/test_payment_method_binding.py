"""Unit tests for payment method binding helpers."""

import os
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from bot.services.payment_method_binding import (
    ATTEMPT_STATUS_PENDING,
    VENMO_SPECIAL_AMOUNT_BINDING_ENV,
    allocate_setup_amount_cents,
    effective_min_cents,
    extract_venmo_handle_from_text,
    extract_venmo_url,
    format_first_time_venmo_setup_message,
    venmo_special_amount_binding_enabled,
)


class TestVenmoSpecialAmountEnv(unittest.TestCase):
    def test_default_off(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(VENMO_SPECIAL_AMOUNT_BINDING_ENV, None)
            self.assertFalse(venmo_special_amount_binding_enabled())

    def test_enabled_values(self):
        for val in ("1", "true", "yes", "on"):
            with patch.dict(os.environ, {VENMO_SPECIAL_AMOUNT_BINDING_ENV: val}):
                self.assertTrue(venmo_special_amount_binding_enabled())


class TestEffectiveMinCents(unittest.TestCase):
    def test_max_of_method_and_tier(self):
        self.assertEqual(
            effective_min_cents(method_min=Decimal("100"), tier_min=Decimal("50")),
            10000,
        )
        self.assertEqual(
            effective_min_cents(method_min=Decimal("50"), tier_min=Decimal("100")),
            10000,
        )

    def test_method_only(self):
        self.assertEqual(
            effective_min_cents(method_min=Decimal("100"), tier_min=None),
            10000,
        )


class TestVenmoExtract(unittest.TestCase):
    def test_url_and_handle(self):
        text = "Venmo: https://venmo.com/u/godfather4444\n• emoji"
        self.assertEqual(
            extract_venmo_url(text),
            "https://venmo.com/u/godfather4444",
        )
        self.assertEqual(
            extract_venmo_handle_from_text(text),
            "@godfather4444",
        )


class TestAllocateSetupAmount(unittest.TestCase):
    def test_first_pending_gets_cent_below_min(self):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.scalar.return_value = 0
        cents = allocate_setup_amount_cents(
            session, variant_id=1, effective_min_cents=10000
        )
        self.assertEqual(cents, 9999)

    def test_second_pending_decrements(self):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.scalar.return_value = 1
        cents = allocate_setup_amount_cents(
            session, variant_id=1, effective_min_cents=10000
        )
        self.assertEqual(cents, 9998)


class TestSetupMessage(unittest.TestCase):
    def test_includes_amounts(self):
        text = format_first_time_venmo_setup_message(
            setup_amount_cents=9999,
            min_display_cents=10000,
            variant_response_text="Venmo: https://venmo.com/u/club-round",
        )
        self.assertIn("$99.99", text)
        self.assertIn("$100.00", text)
        self.assertIn("FIRST-TIME VENMO SETUP", text)
        self.assertIn("venmo.com/u/club-round", text)


class TestMatchPendingInSession(unittest.TestCase):
    def test_match_finds_attempt_by_handle(self):
        from datetime import datetime, timedelta, timezone

        from db.models import PaymentMethodBindAttempt

        attempt = PaymentMethodBindAttempt(
            id=7,
            telegram_chat_id=-1001,
            club_id=2,
            payment_method_slug="venmo",
            method_id=10,
            variant_id=3,
            amount_cents=9999,
            status=ATTEMPT_STATUS_PENDING,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        variant = MagicMock()
        variant.response_text = "Venmo: https://venmo.com/u/club-round"
        variant.response_caption = None

        session = MagicMock()
        session.query.return_value.filter_by.return_value.filter.return_value.order_by.return_value.all.return_value = [
            attempt
        ]
        session.query.return_value.get.return_value = variant

        from bot.services.payment_method_binding import (
            match_pending_venmo_setup_in_session,
        )

        found = match_pending_venmo_setup_in_session(
            session, amount_cents=9999, venmo_handle="@club-round"
        )
        self.assertIs(found, attempt)


if __name__ == "__main__":
    unittest.main()
