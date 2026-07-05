"""Tests for /add parsing and confirmation formatting."""

from decimal import Decimal
import unittest

from bot.services.mtproto_group_add import (
    format_add_confirmation,
    parse_add_command,
)


class ParseAddCommandTests(unittest.TestCase):
    def test_decimal_amount(self) -> None:
        parsed = parse_add_command("/add 21.1")
        self.assertIsNotNone(parsed)
        amount, bonus, name = parsed
        self.assertEqual(amount, Decimal("21.1"))
        self.assertIsNone(bonus)
        self.assertIsNone(name)

    def test_decimal_shorthand(self) -> None:
        parsed = parse_add_command("/21.1")
        self.assertIsNotNone(parsed)
        amount, bonus, name = parsed
        self.assertEqual(amount, Decimal("21.1"))
        self.assertIsNone(bonus)
        self.assertIsNone(name)

    def test_decimal_amount_with_bonus(self) -> None:
        parsed = parse_add_command("/add 21.1 5.5 Jacob")
        self.assertIsNotNone(parsed)
        amount, bonus, name = parsed
        self.assertEqual(amount, Decimal("21.1"))
        self.assertEqual(bonus, Decimal("5.5"))
        self.assertEqual(name, "Jacob")

    def test_whole_amount_unchanged(self) -> None:
        parsed = parse_add_command("/add 500")
        self.assertIsNotNone(parsed)
        amount, bonus, name = parsed
        self.assertEqual(amount, Decimal("500"))
        self.assertIsNone(bonus)
        self.assertIsNone(name)


class FormatAddConfirmationTests(unittest.TestCase):
    def test_decimal_amount_not_truncated(self) -> None:
        text = format_add_confirmation(Decimal("21.1"))
        self.assertIn("21.1 chips", text)
        self.assertNotIn("21 chips", text)

    def test_whole_amount_no_decimal_suffix(self) -> None:
        text = format_add_confirmation(Decimal("500"))
        self.assertIn("500 chips", text)
        self.assertNotIn("500.0", text)

    def test_decimal_bonus_not_truncated(self) -> None:
        text = format_add_confirmation(Decimal("100"), Decimal("5.5"))
        self.assertIn("5.5 bonus", text)


if __name__ == "__main__":
    unittest.main()
