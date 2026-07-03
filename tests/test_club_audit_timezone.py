"""Tests for per-club audit timezone policies."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from api.club_audit_timezone import (
    AuditTimezonePolicy,
    UnknownClubSlugError,
    audit_day_bounds_utc,
    audit_day_window_utc,
    audit_timezone_for_slug,
    audit_timezone_label,
    occurred_at_in_audit_day,
    parse_row_datetime,
    period_timezone_warning,
)


class ClubAuditTimezoneTestCase(unittest.TestCase):
    def test_audit_timezone_for_slug_round_table(self):
        self.assertEqual(
            audit_timezone_for_slug("round-table"),
            AuditTimezonePolicy.FIXED_UTC_MINUS_4,
        )

    def test_audit_timezone_for_slug_creator_club(self):
        self.assertEqual(
            audit_timezone_for_slug("creator-club"),
            AuditTimezonePolicy.FIXED_UTC_MINUS_4,
        )

    def test_audit_timezone_for_slug_clubgto(self):
        self.assertEqual(
            audit_timezone_for_slug("clubgto"),
            AuditTimezonePolicy.FIXED_UTC_MINUS_5,
        )

    def test_unknown_slug_raises(self):
        with self.assertRaises(UnknownClubSlugError):
            audit_timezone_for_slug("club-elevate")

    def test_audit_timezone_labels(self):
        self.assertEqual(
            audit_timezone_label(AuditTimezonePolicy.FIXED_UTC_MINUS_4),
            "UTC-4",
        )
        self.assertEqual(
            audit_timezone_label(AuditTimezonePolicy.FIXED_UTC_MINUS_5),
            "UTC-5",
        )

    def test_fixed_utc_minus_4_no_dst_shift(self):
        summer_start, summer_end = audit_day_bounds_utc("round-table", "2026-06-19")
        winter_start, winter_end = audit_day_bounds_utc("round-table", "2026-01-15")
        self.assertEqual(summer_start, datetime(2026, 6, 19, 4, 0, tzinfo=timezone.utc))
        self.assertEqual(winter_start, datetime(2026, 1, 15, 4, 0, tzinfo=timezone.utc))
        self.assertEqual(
            summer_end, datetime(2026, 6, 20, 3, 59, 59, 999999, tzinfo=timezone.utc)
        )
        self.assertEqual(
            winter_end, datetime(2026, 1, 16, 3, 59, 59, 999999, tzinfo=timezone.utc)
        )

    def test_fixed_utc_minus_5_no_dst_shift(self):
        summer_start, summer_end = audit_day_bounds_utc("clubgto", "2026-06-19")
        winter_start, winter_end = audit_day_bounds_utc("clubgto", "2026-01-15")
        self.assertEqual(summer_start, datetime(2026, 6, 19, 5, 0, tzinfo=timezone.utc))
        self.assertEqual(winter_start, datetime(2026, 1, 15, 5, 0, tzinfo=timezone.utc))
        self.assertEqual(
            summer_end, datetime(2026, 6, 20, 4, 59, 59, 999999, tzinfo=timezone.utc)
        )
        self.assertEqual(
            winter_end, datetime(2026, 1, 16, 4, 59, 59, 999999, tzinfo=timezone.utc)
        )

    def test_summer_date_round_table_vs_clubgto_differ_by_one_hour(self):
        rt_start, _ = audit_day_window_utc("round-table", "2026-06-21")
        gto_start, _ = audit_day_window_utc("clubgto", "2026-06-21")
        self.assertEqual(rt_start, datetime(2026, 6, 21, 4, 0, tzinfo=timezone.utc))
        self.assertEqual(gto_start, datetime(2026, 6, 21, 5, 0, tzinfo=timezone.utc))
        self.assertEqual((gto_start - rt_start).total_seconds(), 3600)

    def test_audit_day_window_includes_grace_hour(self):
        _, end = audit_day_window_utc("round-table", "2026-06-21")
        self.assertEqual(end, datetime(2026, 6, 22, 4, 59, 59, 999999, tzinfo=timezone.utc))

    def test_row_at_1130pm_local_stays_in_audit_day(self):
        policy = audit_timezone_for_slug("clubgto")
        audit_d = date(2026, 6, 21)
        parsed = parse_row_datetime("23:30:00", audit_d, policy)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertTrue(occurred_at_in_audit_day(parsed, "clubgto", audit_d))

        rt_policy = audit_timezone_for_slug("round-table")
        rt_parsed = parse_row_datetime("23:30:00", audit_d, rt_policy)
        self.assertIsNotNone(rt_parsed)
        assert rt_parsed is not None
        self.assertTrue(occurred_at_in_audit_day(rt_parsed, "round-table", audit_d))

    def test_parse_row_datetime_returns_utc(self):
        policy = audit_timezone_for_slug("clubgto")
        parsed = parse_row_datetime(
            datetime(2026, 6, 21, 14, 30, 0),
            date(2026, 6, 21),
            policy,
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(parsed, datetime(2026, 6, 21, 19, 30, tzinfo=timezone.utc))

    def test_period_timezone_warning_on_mismatch_clubgto(self):
        warning = period_timezone_warning(
            "2026-06-21 ~ 2026-06-21 (UTC-4:00)",
            "clubgto",
        )
        self.assertIsNotNone(warning)
        assert warning is not None
        self.assertIn("clubgto", warning)

    def test_period_timezone_warning_on_mismatch_round_table(self):
        warning = period_timezone_warning(
            "2026-06-21 ~ 2026-06-21 (UTC-5:00)",
            "round-table",
        )
        self.assertIsNotNone(warning)
        assert warning is not None
        self.assertIn("round-table", warning)

    def test_period_timezone_warning_none_when_matches(self):
        warning = period_timezone_warning(
            "2026-06-21 ~ 2026-06-21 (UTC-5:00)",
            "clubgto",
        )
        self.assertIsNone(warning)
        warning_rt = period_timezone_warning(
            "2026-06-21 ~ 2026-06-21 (UTC-4:00)",
            "round-table",
        )
        self.assertIsNone(warning_rt)


if __name__ == "__main__":
    unittest.main()
