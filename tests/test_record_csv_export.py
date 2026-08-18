"""Tests for time-range CSV exports."""

from __future__ import annotations

import csv
import io
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from api.record_csv_export import (
    escape_csv_cell,
    parse_inclusive_date_range,
    rows_to_csv_bytes,
    time_bucket_et,
    _ticket_clock_fields,
)


class RecordCsvExportHelpersTestCase(unittest.TestCase):
    def test_parse_inclusive_date_range(self):
        start, end = parse_inclusive_date_range("2026-07-17", "2026-07-26")
        self.assertEqual(start, date(2026, 7, 17))
        self.assertEqual(end, date(2026, 7, 26))

    def test_parse_inclusive_date_range_rejects_inverted(self):
        with self.assertRaises(ValueError):
            parse_inclusive_date_range("2026-07-26", "2026-07-17")

    def test_escape_csv_cell_quotes_commas(self):
        self.assertEqual(escape_csv_cell('say "hi", there'), '"say ""hi"", there"')

    def test_rows_to_csv_bytes_round_trip(self):
        raw = rows_to_csv_bytes(["a", "b"], [["1", "two"], ["3", "four"]])
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
        self.assertEqual(rows[0], ["a", "b"])
        self.assertEqual(rows[1], ["1", "two"])

    def test_frt_over_threshold(self):
        from api.record_csv_export import frt_over_threshold

        self.assertTrue(frt_over_threshold(None, 300))
        self.assertTrue(frt_over_threshold(301, 300))
        self.assertFalse(frt_over_threshold(300, 300))
        self.assertFalse(frt_over_threshold(12, 300))

    def test_time_bucket_et(self):
        self.assertEqual(time_bucket_et(10), "9am-11am")
        self.assertEqual(time_bucket_et(23), "11pm-1am")
        self.assertEqual(time_bucket_et(2), "1am-3am")

    def test_ticket_clock_fields_prefers_customer_first(self):
        fields = _ticket_clock_fields(
            {
                "customer_first_message": "2026-07-17T19:54:26+00:00",
                "resolution": "2026-07-17T19:59:36+00:00",
            }
        )
        self.assertEqual(fields["clock_source"], "customer_first_message")
        self.assertEqual(fields["hour_et"], 15)
        self.assertEqual(fields["dow_et"], "Friday")
        self.assertEqual(fields["time_bucket_et"], "3pm-5pm")


class RecordCsvExportBuildTestCase(unittest.TestCase):
    def test_build_bonus_records_csv_empty(self):
        from api.record_csv_export import build_bonus_records_csv

        session = MagicMock()
        session.query.return_value.options.return_value.filter.return_value.order_by.return_value.all.return_value = []
        session.query.return_value.all.return_value = []

        content = build_bonus_records_csv(
            session,
            from_day=date(2026, 7, 17),
            to_day=date(2026, 7, 17),
        )
        rows = list(csv.reader(io.StringIO(content.decode("utf-8"))))
        self.assertEqual(rows[0][0], "id")
        self.assertEqual(len(rows), 1)

    def test_build_bonus_records_csv_row(self):
        from api.record_csv_export import build_bonus_records_csv

        record = MagicMock()
        record.id = 1
        record.created_at = datetime(2026, 7, 17, 12, 0, 0)
        record.club_id = 4
        record.player_username = "player"
        record.gg_player_id = "1234-5678"
        record.group_title = "GTO / 1234-5678 / Player"
        record.amount = Decimal("25.00")
        record.bonus_type = MagicMock(name="Referral")
        record.bonus_type.name = "Referral"
        record.custom_description = None
        record.admin_telegram_user_id = None
        record.chat_id = -100
        record.player_details_id = None

        session = MagicMock()
        club_row = MagicMock(id=4, name="ClubGTO")

        def query_side_effect(*args, **kwargs):
            target = args[0] if args else None
            name = getattr(target, "__name__", str(target))
            q = MagicMock()
            if name == "Club" or "Club" in str(args):
                q.all.return_value = [club_row]
            else:
                q.options.return_value.filter.return_value.order_by.return_value.all.return_value = [
                    record
                ]
            return q

        session.query.side_effect = query_side_effect

        content = build_bonus_records_csv(
            session,
            from_day=date(2026, 7, 17),
            to_day=date(2026, 7, 17),
        )
        rows = list(csv.reader(io.StringIO(content.decode("utf-8"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], "1")
        self.assertEqual(rows[1][7], "25.00")
        self.assertEqual(rows[1][8], "Referral")


if __name__ == "__main__":
    unittest.main()
