import unittest

from bot.services.clubgg_deposit_api import (
    _resolve_round_table_union_for_auto_chip_add,
    _resolve_round_table_union_shorthand,
    _resolve_union_for_auto_chip_add,
    resolve_clubgg_club_name,
)
from bot.services.player_details import (
    merge_union_prefix,
    shorthand_tokens_for_club_resolve,
)
from bot.services.round_table_unions import (
    CREATOR_CLUB_DEPOSIT_UNIONS,
    home_union_for_club_name,
    union_label_for_shorthand,
    union_shorthands_for_club_name,
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


class TestCreatorClubAcesUnionResolution(unittest.TestCase):
    """Creator Club players may route chips to Aces Table (Massiv)."""

    def test_resolve_clubgg_club_name_creator_club(self) -> None:
        self.assertEqual(
            resolve_clubgg_club_name("Creator Club", "AT"), "Aces Table"
        )
        self.assertEqual(
            resolve_clubgg_club_name("Creator Club", "CC"), "Creator Club"
        )
        # Unknown/garbage union must never silently reroute to Aces.
        self.assertEqual(
            resolve_clubgg_club_name("Creator Club", "XX"), "Creator Club"
        )

    def test_cc_only_title_stays_creator_club(self) -> None:
        self.assertEqual(
            _resolve_union_for_auto_chip_add(
                "Creator Club", "CC / 1234-5678 / Player", None
            ),
            "CC",
        )

    def test_cc_at_title_uses_recorded_choice(self) -> None:
        self.assertEqual(
            _resolve_union_for_auto_chip_add(
                "Creator Club", "CC AT / 1234-5678 / Player", "AT"
            ),
            "AT",
        )
        self.assertEqual(
            _resolve_union_for_auto_chip_add(
                "Creator Club", "CC AT / 1234-5678 / Player", "CC"
            ),
            "CC",
        )

    def test_legacy_cc_at_title_without_choice_defaults_creator_club(self) -> None:
        """Groups renamed CC AT for audit must keep behaving as they do today."""
        self.assertEqual(
            _resolve_union_for_auto_chip_add(
                "Creator Club", "CC AT / 1234-5678 / Player", None
            ),
            "CC",
        )

    def test_rt_union_never_leaks_into_creator_club(self) -> None:
        self.assertEqual(
            _resolve_union_for_auto_chip_add(
                "Creator Club", "CC AT / 1234-5678 / Player", "RT"
            ),
            "CC",
        )

    def test_clubs_without_unions_return_none(self) -> None:
        self.assertIsNone(
            _resolve_union_for_auto_chip_add(
                "ClubGTO", "GTO / 1234-5678 / Player", "AT"
            )
        )

    def test_round_table_behaviour_unchanged(self) -> None:
        self.assertEqual(
            _resolve_union_for_auto_chip_add(
                "Round Table", "RT AT / 1234-5678 / Player", None
            ),
            "RT",
        )
        self.assertEqual(
            _resolve_union_for_auto_chip_add(
                "Round Table", "AT / 1234-5678 / Player", "RT"
            ),
            "AT",
        )


class TestCreatorClubUnionConfig(unittest.TestCase):
    def test_creator_club_offers_cc_and_at(self) -> None:
        self.assertEqual(
            [u["shorthand"] for u in CREATOR_CLUB_DEPOSIT_UNIONS], ["CC", "AT"]
        )
        self.assertEqual(
            union_shorthands_for_club_name("Creator Club"), frozenset({"CC", "AT"})
        )
        self.assertEqual(
            union_shorthands_for_club_name("Round Table"), frozenset({"RT", "AT"})
        )
        self.assertEqual(union_shorthands_for_club_name("ClubGTO"), frozenset())

    def test_home_unions(self) -> None:
        self.assertEqual(home_union_for_club_name("Creator Club"), "CC")
        self.assertEqual(home_union_for_club_name("Round Table"), "RT")
        self.assertIsNone(home_union_for_club_name("ClubGTO"))

    def test_labels(self) -> None:
        self.assertEqual(union_label_for_shorthand("CC"), "Creator Club (TMT Union)")
        self.assertEqual(
            union_label_for_shorthand("AT"), "Aces Table (Massiv Union)"
        )
        self.assertEqual(
            union_label_for_shorthand("RT"), "Round Table (TMT Union)"
        )
        self.assertIsNone(union_label_for_shorthand("XX"))


class TestCreatorClubAcesTitleMerge(unittest.TestCase):
    """The group title must pick up the AT tag exactly like RT does."""

    def test_cc_title_gains_at_tag(self) -> None:
        self.assertEqual(
            merge_union_prefix("CC / 8272-5942 / @player", "AT"),
            "CC AT / 8272-5942 / @player",
        )

    def test_choosing_creator_club_does_not_rename(self) -> None:
        self.assertIsNone(merge_union_prefix("CC / 8272-5942 / @player", "CC"))

    def test_already_tagged_title_is_left_alone(self) -> None:
        self.assertIsNone(merge_union_prefix("CC AT / 8272-5942 / @player", "AT"))

    def test_cc_at_title_still_resolves_to_creator_club(self) -> None:
        self.assertEqual(shorthand_tokens_for_club_resolve("CC AT"), ["CC", "AT"])
        self.assertEqual(shorthand_tokens_for_club_resolve("AT CC"), ["CC", "AT"])


if __name__ == "__main__":
    unittest.main()
