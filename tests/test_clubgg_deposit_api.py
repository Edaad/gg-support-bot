import unittest

from bot.services.clubgg_deposit_api import (
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
