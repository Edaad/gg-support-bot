"""Tests for crypto alert scope mapping and bind validation."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bot.services.crypto_payments import (
    ALERT_NAME_CLUBGTO,
    ALERT_NAME_RT_AT_CC,
    ALERT_SCOPE_CLUBGTO,
    ALERT_SCOPE_RT_AT_CC,
    alert_scope_for_club_name,
    resolve_alert_scope,
    validate_bind_alert_scope,
)
from bot.services.venmo_payments import BindResult


class CryptoAlertScopeTestCase(unittest.TestCase):
    def test_resolve_alert_scope_clubgto(self):
        self.assertEqual(resolve_alert_scope(ALERT_NAME_CLUBGTO), ALERT_SCOPE_CLUBGTO)

    def test_resolve_alert_scope_rt_at_cc(self):
        self.assertEqual(resolve_alert_scope(ALERT_NAME_RT_AT_CC), ALERT_SCOPE_RT_AT_CC)

    def test_resolve_alert_scope_case_insensitive(self):
        self.assertEqual(
            resolve_alert_scope("clubgto crypto payment"),
            ALERT_SCOPE_CLUBGTO,
        )

    def test_resolve_alert_scope_unknown_rejected(self):
        with self.assertRaises(ValueError):
            resolve_alert_scope("Other Crypto Payment")

    def test_club_name_to_alert_scope(self):
        self.assertEqual(alert_scope_for_club_name("ClubGTO"), ALERT_SCOPE_CLUBGTO)
        self.assertEqual(alert_scope_for_club_name("Round Table"), ALERT_SCOPE_RT_AT_CC)
        self.assertEqual(alert_scope_for_club_name("Creator Club"), ALERT_SCOPE_RT_AT_CC)
        self.assertIsNone(alert_scope_for_club_name("Some Other Club"))

    def test_validate_bind_scope_clubgto_mismatch(self):
        payment = MagicMock()
        payment.alert_scope = ALERT_SCOPE_CLUBGTO
        with patch("bot.services.crypto_payments.get_db") as mock_get_db:
            session = MagicMock()
            mock_get_db.return_value.__enter__.return_value = session
            club = MagicMock()
            club.name = "Round Table"
            session.query.return_value.filter_by.return_value.one_or_none.return_value = club
            result = validate_bind_alert_scope(payment, bound_club_id=2)
        self.assertIsInstance(result, BindResult)
        assert result is not None
        self.assertFalse(result.ok)
        self.assertIn("ClubGTO", result.error or "")

    def test_validate_bind_scope_match(self):
        payment = MagicMock()
        payment.alert_scope = ALERT_SCOPE_RT_AT_CC
        with patch("bot.services.crypto_payments.get_db") as mock_get_db:
            session = MagicMock()
            mock_get_db.return_value.__enter__.return_value = session
            club = MagicMock()
            club.name = "Creator Club"
            session.query.return_value.filter_by.return_value.one_or_none.return_value = club
            result = validate_bind_alert_scope(payment, bound_club_id=3)
        self.assertIsNone(result)


class FormatPaidAtDisplayTestCase(unittest.TestCase):
    def test_iso_z_suffix(self):
        from bot.services.venmo_payments import format_paid_at_display

        self.assertEqual(
            format_paid_at_display("2026-06-06T21:21:35Z"),
            "Jun 06, 2026 09:21 PM UTC",
        )

    def test_unparseable_passthrough(self):
        from bot.services.venmo_payments import format_paid_at_display

        self.assertEqual(format_paid_at_display("not-a-date"), "not-a-date")


if __name__ == "__main__":
    unittest.main()
