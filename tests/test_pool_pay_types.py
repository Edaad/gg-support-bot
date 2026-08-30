"""Tests for pool pay slug helpers."""

import unittest

from bot.services.pool_pay_types import (
    build_pool_pay_slug,
    normalize_identifier_suffix,
    parse_pool_pay_slug,
    pool_pay_type_from_method,
    validate_pool_pay_type,
)


class TestPoolPayTypes(unittest.TestCase):
    def test_build_union_slug(self):
        self.assertEqual(
            build_pool_pay_slug("zelle", "union_method", "yaki"),
            "zelle-union-yaki",
        )

    def test_build_large_cashout_slug(self):
        self.assertEqual(
            build_pool_pay_slug("cashapp", "large_cashout", "main-rt"),
            "cashapp-lc-main-rt",
        )

    def test_parse_structured_slug(self):
        self.assertEqual(
            parse_pool_pay_slug("venmo-lc-yaki"),
            ("venmo", "large_cashout", "yaki"),
        )

    def test_parse_legacy_slug_returns_none(self):
        self.assertIsNone(parse_pool_pay_slug("main-zelle-rt"))

    def test_normalize_suffix(self):
        self.assertEqual(normalize_identifier_suffix("  Yaki-RT  "), "yaki-rt")

    def test_validate_pool_pay_type(self):
        self.assertEqual(validate_pool_pay_type("large_cashout"), "large_cashout")
        with self.assertRaises(ValueError):
            validate_pool_pay_type("invalid")

    def test_pool_pay_type_from_method_column(self):
        class _Method:
            pool_pay_type = "large_cashout"
            slug = "zelle-union-old"

        self.assertEqual(pool_pay_type_from_method(_Method()), "large_cashout")

    def test_pool_pay_type_from_slug_fallback(self):
        class _Method:
            pool_pay_type = None
            slug = "zelle-lc-yaki"

        self.assertEqual(pool_pay_type_from_method(_Method()), "large_cashout")


if __name__ == "__main__":
    unittest.main()
