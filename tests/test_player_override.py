"""Tests for /override player_details binding."""

from __future__ import annotations

import unittest

from bot.services import player_details as pd


class PlayerOverrideTestCase(unittest.TestCase):
    def test_invalid_player_id_format(self):
        res = pd.override_chat_for_player(club_id=1, gg_player_id="bad", chat_id=-100123)
        self.assertFalse(res.ok)
        self.assertIn("Invalid player id", res.error or "")


if __name__ == "__main__":
    unittest.main()
