import unittest

from bot.services.clubgg_deposit_api import (
    _resolve_round_table_union_for_auto_chip_add,
    _resolve_round_table_union_shorthand,
    resolve_clubgg_club_name,
)


class TestRoundTableUnionResolution(unittest.TestCase):
    def test_defaults_rt_when_missing(self) -> None:
        self.assertEqual(_resolve_round_table_union_shorthand(None), "RT")
        self.assertEqual(_resolve_round_table_union_shorthand(""), "RT")
        self.assertEqual(_resolve_round_table_union_shorthand("  "), "RT")

    def test_uses_stored_rt_or_at(self) -> None:
        self.assertEqual(_resolve_round_table_union_shorthand("RT"), "RT")
        self.assertEqual(_resolve_round_table_union_shorthand("rt"), "RT")
        self.assertEqual(_resolve_round_table_union_shorthand("AT"), "AT")
        self.assertEqual(_resolve_round_table_union_shorthand(" at "), "AT")

    def test_invalid_stored_union_defaults_rt(self) -> None:
        self.assertEqual(_resolve_round_table_union_shorthand("XX"), "RT")

    def test_title_at_only_routes_to_aces(self) -> None:
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(
                "AT / 1234-5678 / Player", None
            ),
            "AT",
        )
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(
                "AT / 1234-5678 / Player", "RT"
            ),
            "AT",
        )

    def test_title_rt_only_routes_to_round_table(self) -> None:
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(
                "RT / 1234-5678 / Player", None
            ),
            "RT",
        )
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(
                "RT / 1234-5678 / Player", "AT"
            ),
            "RT",
        )

    def test_title_both_uses_deposit_union(self) -> None:
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(
                "RT AT / 1234-5678 / Player", "AT"
            ),
            "AT",
        )
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(
                "RT AT / 8190-5287 / ThePirate343", "RT"
            ),
            "RT",
        )
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(
                "RT AT / 1234-5678 / Player", None
            ),
            "RT",
        )

    def test_no_title_unions_falls_back_to_deposit_union(self) -> None:
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(None, "AT"),
            "AT",
        )
        self.assertEqual(
            _resolve_round_table_union_for_auto_chip_add(
                "GTO / 1234-5678 / Player", "AT"
            ),
            "AT",
        )

    def test_resolve_clubgg_club_name_round_table(self) -> None:
        self.assertEqual(
            resolve_clubgg_club_name("Round Table", "RT"), "Round Table"
        )
        self.assertEqual(
            resolve_clubgg_club_name("Round Table", "AT"), "Aces Table"
        )
        self.assertIsNone(resolve_clubgg_club_name("Round Table", None))

    def test_resolve_clubgg_club_name_non_union_clubs(self) -> None:
        self.assertEqual(resolve_clubgg_club_name("ClubGTO", None), "ClubGTO")
        self.assertEqual(
            resolve_clubgg_club_name("Creator Club", None), "Creator Club"
        )


if __name__ == "__main__":
    unittest.main()
