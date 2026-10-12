"""Date parameter parsing: ET calendar days vs UTC datetimes."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from mcp.server.mcpserver.exceptions import ToolError

from mcp_server.dates import (
    ET,
    et_day_end_utc,
    et_day_start_utc,
    parse_et_day,
    parse_range,
    parse_when,
    today_et,
    utc_now_iso,
)

UTC = timezone.utc


class ParseWhenTests(unittest.TestCase):
    def test_empty_values_return_none(self) -> None:
        self.assertIsNone(parse_when(None))
        self.assertIsNone(parse_when(""))
        self.assertIsNone(parse_when("   "))

    def test_et_day_during_dst_is_utc_minus_four(self) -> None:
        start = parse_when("2026-07-01")
        self.assertEqual(start, datetime(2026, 7, 1, 4, 0, tzinfo=UTC))
        self.assertEqual(start.tzinfo, UTC)
        end = parse_when("2026-07-01", end=True)
        self.assertEqual(end, datetime(2026, 7, 2, 4, 0, tzinfo=UTC))

    def test_et_day_outside_dst_is_utc_minus_five(self) -> None:
        start = parse_when("2026-01-15")
        self.assertEqual(start, datetime(2026, 1, 15, 5, 0, tzinfo=UTC))
        end = parse_when("2026-01-15", end=True)
        self.assertEqual(end, datetime(2026, 1, 16, 5, 0, tzinfo=UTC))

    def test_et_helpers_match_parse_when(self) -> None:
        day = date(2026, 7, 1)
        self.assertEqual(et_day_start_utc(day), parse_when("2026-07-01"))
        self.assertEqual(et_day_end_utc(day), parse_when("2026-07-01", end=True))
        self.assertEqual(
            et_day_start_utc(day).astimezone(ET).replace(tzinfo=None),
            datetime(2026, 7, 1, 0, 0),
        )

    def test_iso_naive_datetime_is_utc(self) -> None:
        parsed = parse_when("2026-07-01T12:30:00")
        self.assertEqual(parsed, datetime(2026, 7, 1, 12, 30, tzinfo=UTC))
        self.assertEqual(parsed.utcoffset().total_seconds(), 0)

    def test_iso_with_z_suffix(self) -> None:
        parsed = parse_when("2026-07-01T12:30:00Z")
        self.assertEqual(parsed, datetime(2026, 7, 1, 12, 30, tzinfo=UTC))

    def test_aware_offset_is_converted_to_utc(self) -> None:
        parsed = parse_when("2026-07-01T08:00:00-04:00")
        self.assertEqual(parsed, datetime(2026, 7, 1, 12, 0, tzinfo=UTC))
        self.assertEqual(parsed.tzinfo, UTC)

    def test_end_flag_does_not_change_datetimes(self) -> None:
        self.assertEqual(
            parse_when("2026-07-01T12:30:00Z", end=True),
            parse_when("2026-07-01T12:30:00Z"),
        )

    def test_naive_strips_tzinfo(self) -> None:
        parsed = parse_when("2026-07-01", naive=True)
        self.assertIsNone(parsed.tzinfo)
        self.assertEqual(parsed, datetime(2026, 7, 1, 4, 0))
        parsed = parse_when("2026-07-01T08:00:00-04:00", naive=True)
        self.assertEqual(parsed, datetime(2026, 7, 1, 12, 0))

    def test_invalid_values_raise_tool_error(self) -> None:
        with self.assertRaises(ToolError):
            parse_when("2026-13-45")
        with self.assertRaises(ToolError):
            parse_when("yesterday")
        with self.assertRaises(ToolError):
            parse_when("2026-07-01T99:00:00")


class ParseRangeTests(unittest.TestCase):
    def test_open_ended_ranges(self) -> None:
        self.assertEqual(parse_range(None, None), (None, None))
        start, stop = parse_range("2026-07-01", None)
        self.assertEqual(start, datetime(2026, 7, 1, 4, 0, tzinfo=UTC))
        self.assertIsNone(stop)
        start, stop = parse_range(None, "2026-07-01")
        self.assertIsNone(start)
        self.assertEqual(stop, datetime(2026, 7, 2, 4, 0, tzinfo=UTC))

    def test_same_day_covers_the_whole_et_day(self) -> None:
        start, stop = parse_range("2026-07-01", "2026-07-01")
        self.assertEqual(start, datetime(2026, 7, 1, 4, 0, tzinfo=UTC))
        self.assertEqual(stop, datetime(2026, 7, 2, 4, 0, tzinfo=UTC))

    def test_naive_range(self) -> None:
        start, stop = parse_range("2026-01-15", "2026-01-16", naive=True)
        self.assertIsNone(start.tzinfo)
        self.assertIsNone(stop.tzinfo)
        self.assertEqual(start, datetime(2026, 1, 15, 5, 0))
        self.assertEqual(stop, datetime(2026, 1, 17, 5, 0))

    def test_inverted_range_raises(self) -> None:
        with self.assertRaises(ToolError) as cm:
            parse_range("2026-07-03", "2026-07-01")
        self.assertIn("from must be on or before to", str(cm.exception))


class EtDayTests(unittest.TestCase):
    def test_parse_et_day(self) -> None:
        self.assertEqual(parse_et_day("2026-07-01"), date(2026, 7, 1))
        self.assertEqual(parse_et_day("2026-07-01T10:00:00"), date(2026, 7, 1))

    def test_parse_et_day_default(self) -> None:
        fallback = date(2026, 1, 1)
        self.assertEqual(parse_et_day(None, default=fallback), fallback)
        self.assertEqual(parse_et_day("", default=fallback), fallback)
        self.assertEqual(parse_et_day("  ", default=fallback), fallback)
        self.assertIsNone(parse_et_day(None))

    def test_parse_et_day_invalid(self) -> None:
        with self.assertRaises(ToolError):
            parse_et_day("07/01/2026")
        with self.assertRaises(ToolError):
            parse_et_day("2026-02-30")
        # Strings shorter than YYYY-MM-DD are not a date: None, not an error.
        self.assertIsNone(parse_et_day("2026-7-1"))

    def test_today_et_returns_a_date(self) -> None:
        today = today_et()
        self.assertIsInstance(today, date)
        self.assertNotIsInstance(today, datetime)
        self.assertLessEqual(abs((today - datetime.now(ET).date()).days), 1)

    def test_utc_now_iso_format(self) -> None:
        value = utc_now_iso()
        self.assertTrue(value.endswith("Z"))
        self.assertEqual(len(value), len("2026-01-01T00:00:00Z"))


if __name__ == "__main__":
    unittest.main()
