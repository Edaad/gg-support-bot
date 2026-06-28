"""Unit tests for gg-computer Mongo player_details list helpers."""

from __future__ import annotations

import unittest

from bot.services.gg_computer_mongo import (
    format_player_details_row,
    list_player_details_rows_for_club,
)


class GgComputerMongoPlayerListTests(unittest.TestCase):
    def test_new_schema_row(self):
        row = format_player_details_row(
            {
                "clubId": "aces-table",
                "gg_id": "3014-1775",
                "nickname": "B00BHawk",
                "agent": "B00BHawk",
            },
            "aces-table",
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["gg_id"], "3014-1775")
        self.assertEqual(row["nickname"], "B00BHawk")

    def test_legacy_schema_row(self):
        row = format_player_details_row(
            {
                "gg_id": "3014-1775",
                "nickname": "B00BHawk",
                "clubs": ["round-table", "aces-table"],
            },
            "aces-table",
        )
        self.assertIsNotNone(row)

    def test_legacy_row_wrong_club_excluded(self):
        row = format_player_details_row(
            {
                "gg_id": "3014-1775",
                "nickname": "B00BHawk",
                "clubs": ["round-table"],
            },
            "aces-table",
        )
        self.assertIsNone(row)

    def test_dedupes_by_gg_id(self):
        players = list_player_details_rows_for_club(
            [
                {"clubId": "aces-table", "gg_id": "1-1", "nickname": "A"},
                {"clubId": "aces-table", "gg_id": "1-1", "nickname": "A2"},
                {"clubId": "aces-table", "gg_id": "2-2", "nickname": "B"},
            ],
            "aces-table",
        )
        self.assertEqual([p["gg_id"] for p in players], ["1-1", "2-2"])


if __name__ == "__main__":
    unittest.main()
