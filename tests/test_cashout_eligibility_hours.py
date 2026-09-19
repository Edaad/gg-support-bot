"""Soft business-hours advisory vs hard cooldown gate for /cashout."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from bot.services import club as club_svc


def _settings(*, cooldown=False, hours=True, start="08:00", end="23:00"):
    return {
        "cooldown_enabled": cooldown,
        "cooldown_hours": 24,
        "hours_enabled": hours,
        "hours_start": start,
        "hours_end": end,
    }


class CheckCashoutEligibilityHoursTests(unittest.TestCase):
    def test_outside_hours_allows_with_advisory(self):
        with patch.object(club_svc, "get_cooldown_settings", return_value=_settings()):
            with patch.object(club_svc, "check_and_consume_bypass", return_value=False):
                with patch.object(club_svc, "_is_within_hours", return_value=False):
                    with patch.object(
                        club_svc, "_hours_range_str", return_value="8 AM - 11 PM"
                    ):
                        ok, msg = club_svc.check_cashout_eligibility(1, -100)
        self.assertTrue(ok)
        self.assertIsNotNone(msg)
        self.assertIn("8 AM - 11 PM", msg)
        self.assertIn("processed during business hours", msg)

    def test_within_hours_allows_without_message(self):
        with patch.object(club_svc, "get_cooldown_settings", return_value=_settings()):
            with patch.object(club_svc, "check_and_consume_bypass", return_value=False):
                with patch.object(club_svc, "_is_within_hours", return_value=True):
                    ok, msg = club_svc.check_cashout_eligibility(1, -100)
        self.assertTrue(ok)
        self.assertIsNone(msg)

    def test_hours_disabled_allows_without_message(self):
        with patch.object(
            club_svc, "get_cooldown_settings", return_value=_settings(hours=False)
        ):
            with patch.object(club_svc, "check_and_consume_bypass", return_value=False):
                ok, msg = club_svc.check_cashout_eligibility(1, -100)
        self.assertTrue(ok)
        self.assertIsNone(msg)

    def test_cooldown_denies_even_outside_hours(self):
        now = datetime.now(timezone.utc)
        last = now - timedelta(hours=1)
        with patch.object(
            club_svc, "get_cooldown_settings", return_value=_settings(cooldown=True)
        ):
            with patch.object(club_svc, "check_and_consume_bypass", return_value=False):
                with patch.object(club_svc, "get_last_activity", return_value=last):
                    with patch.object(club_svc, "_is_within_hours", return_value=False):
                        ok, msg = club_svc.check_cashout_eligibility(1, -100)
        self.assertFalse(ok)
        self.assertIsNotNone(msg)
        self.assertIn("must wait", msg)
        self.assertNotIn("processed during business hours", msg)


if __name__ == "__main__":
    unittest.main()
