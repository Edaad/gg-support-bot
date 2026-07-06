"""Tests for /lmk command parsing."""

import unittest

from bot.services.mtproto_group_lmk import parse_lmk_command


class ParseLmkCommandTests(unittest.TestCase):
    def test_plain_lmk(self) -> None:
        self.assertTrue(parse_lmk_command("/lmk"))

    def test_lmk_with_bot_username(self) -> None:
        self.assertTrue(parse_lmk_command("/lmk@SomeBot"))

    def test_lmk_with_trailing_space(self) -> None:
        self.assertTrue(parse_lmk_command("/lmk "))

    def test_rejects_other_commands(self) -> None:
        self.assertFalse(parse_lmk_command("/add 500"))
        self.assertFalse(parse_lmk_command("/lmkextra"))


if __name__ == "__main__":
    unittest.main()
