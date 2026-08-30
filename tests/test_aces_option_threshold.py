"""Deposit-count threshold for the Creator Club / Aces Table picker."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bot.services.round_table_unions import (
    CREATOR_CLUB_DEPOSIT_UNIONS,
    ROUND_TABLE_DEPOSIT_UNIONS,
    cashout_unions_for_chat,
    deposit_unions_for_chat,
    has_aces_deposit_history,
)

MOD = "bot.services.round_table_unions"
CC_ID = 3
RT_ID = 2
GTO_ID = 4
CHAT_ID = -100123

_CLUBS = {
    CC_ID: SimpleNamespace(id=CC_ID, name="Creator Club"),
    RT_ID: SimpleNamespace(id=RT_ID, name="Round Table"),
    GTO_ID: SimpleNamespace(id=GTO_ID, name="ClubGTO"),
}


def _env(*, threshold=0, deposits=0, ack=False, title=None):
    """Patch every DB-backed lookup the union gates touch."""
    return (
        patch(f"{MOD}.get_club_by_id", side_effect=lambda cid: _CLUBS.get(int(cid))),
        patch(f"{MOD}.get_aces_option_min_deposits", return_value=threshold),
        patch(f"{MOD}.count_deposits_for_chat", return_value=deposits),
        patch(f"{MOD}.has_aces_join_ack", return_value=ack),
        patch(f"{MOD}.get_group_title_for_chat", return_value=(title, CC_ID)),
    )


class _GateTestCase(unittest.TestCase):
    def _call(self, fn, club_id, chat_id=CHAT_ID, **env):
        patches = _env(**env)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return fn(club_id, chat_id)


class DepositUnionThresholdTests(_GateTestCase):
    def test_threshold_zero_always_offers_the_picker(self):
        self.assertEqual(
            self._call(deposit_unions_for_chat, CC_ID, threshold=0, deposits=0),
            CREATOR_CLUB_DEPOSIT_UNIONS,
        )

    def test_below_threshold_hides_the_picker(self):
        self.assertIsNone(
            self._call(deposit_unions_for_chat, CC_ID, threshold=5, deposits=3)
        )

    def test_at_threshold_offers_the_picker(self):
        self.assertEqual(
            self._call(deposit_unions_for_chat, CC_ID, threshold=5, deposits=5),
            CREATOR_CLUB_DEPOSIT_UNIONS,
        )

    def test_existing_aces_player_keeps_picker_below_threshold(self):
        """Raising the threshold must not silently reroute an Aces player's chips."""
        self.assertEqual(
            self._call(
                deposit_unions_for_chat, CC_ID, threshold=5, deposits=0, ack=True
            ),
            CREATOR_CLUB_DEPOSIT_UNIONS,
        )

    def test_legacy_cc_at_title_keeps_picker_below_threshold(self):
        self.assertEqual(
            self._call(
                deposit_unions_for_chat,
                CC_ID,
                threshold=5,
                deposits=0,
                ack=False,
                title="CC AT / 1234-5678 / Player",
            ),
            CREATOR_CLUB_DEPOSIT_UNIONS,
        )

    def test_round_table_ignores_the_threshold(self):
        self.assertEqual(
            self._call(deposit_unions_for_chat, RT_ID, threshold=5, deposits=0),
            ROUND_TABLE_DEPOSIT_UNIONS,
        )

    def test_club_without_unions_still_gets_no_picker(self):
        self.assertIsNone(
            self._call(deposit_unions_for_chat, GTO_ID, threshold=0, deposits=99)
        )

    def test_unknown_chat_falls_back_to_club_level(self):
        self.assertEqual(
            self._call(
                deposit_unions_for_chat, CC_ID, chat_id=None, threshold=5, deposits=0
            ),
            CREATOR_CLUB_DEPOSIT_UNIONS,
        )


class CashoutUnionTests(_GateTestCase):
    def test_creator_club_without_aces_history_gets_no_picker(self):
        self.assertIsNone(
            self._call(cashout_unions_for_chat, CC_ID, threshold=0, deposits=99)
        )

    def test_creator_club_with_ack_gets_the_picker(self):
        self.assertEqual(
            self._call(cashout_unions_for_chat, CC_ID, ack=True),
            CREATOR_CLUB_DEPOSIT_UNIONS,
        )

    def test_creator_club_with_legacy_cc_at_title_gets_the_picker(self):
        self.assertEqual(
            self._call(
                cashout_unions_for_chat, CC_ID, title="CC AT / 1234-5678 / Player"
            ),
            CREATOR_CLUB_DEPOSIT_UNIONS,
        )

    def test_deposit_count_alone_does_not_unlock_cashout_aces(self):
        """Cashout is gated on real Aces history, not the deposit threshold."""
        self.assertIsNone(
            self._call(cashout_unions_for_chat, CC_ID, threshold=1, deposits=500)
        )

    def test_round_table_always_offers_both(self):
        self.assertEqual(
            self._call(cashout_unions_for_chat, RT_ID),
            ROUND_TABLE_DEPOSIT_UNIONS,
        )


class AcesHistoryTests(unittest.TestCase):
    def test_plain_cc_title_is_not_aces_history(self):
        with patch(f"{MOD}.has_aces_join_ack", return_value=False), patch(
            f"{MOD}.get_group_title_for_chat",
            return_value=("CC / 1234-5678 / Player", CC_ID),
        ):
            self.assertFalse(has_aces_deposit_history(CHAT_ID))

    def test_lookup_failure_is_treated_as_no_history(self):
        with patch(f"{MOD}.has_aces_join_ack", side_effect=RuntimeError("db down")):
            self.assertFalse(has_aces_deposit_history(CHAT_ID))


if __name__ == "__main__":
    unittest.main()
