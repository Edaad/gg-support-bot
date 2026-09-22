"""Tests for Venmo variant destination fields and default template."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import MagicMock

from bot.services.venmo_variant_fields import (
    MEMO_HIGH,
    MEMO_LOW,
    MEMO_MEDIUM,
    build_default_venmo_response_text,
    extract_unique_venmo_link_from_text,
    memo_bucket_for_amount,
    pick_venmo_memo,
    stored_venmo_tag,
    text_contains_venmo_link,
    validate_venmo_link,
    validate_venmo_response_mode,
    validate_venmo_tag,
    validate_venmo_tag_and_link,
)
from bot.services.payment_method_binding import (
    format_first_time_payment_destination_message,
)


class ValidateVenmoFieldsTests(unittest.TestCase):
    def test_tag_accepts_at_handle(self):
        self.assertEqual(validate_venmo_tag("@GodFather_44"), "@godfather_44")

    def test_tag_adds_at(self):
        self.assertEqual(validate_venmo_tag("club-round"), "@club-round")

    def test_tag_rejects_bad(self):
        with self.assertRaises(ValueError):
            validate_venmo_tag("@")
        with self.assertRaises(ValueError):
            validate_venmo_tag("@bad space")
        with self.assertRaises(ValueError):
            validate_venmo_tag("a")

    def test_link_accepts_canonical(self):
        self.assertEqual(
            validate_venmo_link("https://Venmo.com/u/Club-Round"),
            "https://venmo.com/u/club-round",
        )

    def test_link_rejects_www_query_slash(self):
        with self.assertRaises(ValueError):
            validate_venmo_link("https://www.venmo.com/u/x")
        with self.assertRaises(ValueError):
            validate_venmo_link("https://venmo.com/u/x?txn=pay")
        with self.assertRaises(ValueError):
            validate_venmo_link("https://venmo.com/u/x/")
        with self.assertRaises(ValueError):
            validate_venmo_link("http://venmo.com/u/x")

    def test_tag_link_must_match(self):
        with self.assertRaises(ValueError) as ctx:
            validate_venmo_tag_and_link("@alice", "https://venmo.com/u/bob")
        self.assertIn("same account", str(ctx.exception))

    def test_tag_link_ok(self):
        tag, link = validate_venmo_tag_and_link("@Alice", "https://venmo.com/u/Alice")
        self.assertEqual(tag, "@alice")
        self.assertEqual(link, "https://venmo.com/u/alice")

    def test_mode(self):
        self.assertEqual(validate_venmo_response_mode("Default"), "default")
        with self.assertRaises(ValueError):
            validate_venmo_response_mode("email")


class MemoBucketTests(unittest.TestCase):
    def test_edges(self):
        self.assertEqual(memo_bucket_for_amount(50), "low")
        self.assertEqual(memo_bucket_for_amount(Decimal("500")), "low")
        self.assertEqual(memo_bucket_for_amount(Decimal("500.01")), "medium")
        self.assertEqual(memo_bucket_for_amount(Decimal("999.99")), "medium")
        self.assertEqual(memo_bucket_for_amount(1000), "high")
        self.assertEqual(memo_bucket_for_amount(5000), "high")

    def test_pick_from_bucket(self):
        self.assertIn(pick_venmo_memo(50), MEMO_LOW)
        self.assertIn(pick_venmo_memo(750), MEMO_MEDIUM)
        self.assertEqual(pick_venmo_memo(1000), MEMO_HIGH[0])


class DefaultTemplateTests(unittest.TestCase):
    def test_wording(self):
        text = build_default_venmo_response_text(
            "https://venmo.com/u/club-round",
            200,
            memo="Electric bill split",
        )
        self.assertIn("Venmo: https://venmo.com/u/club-round", text)
        self.assertIn("friends and family", text)
        self.assertIn("(Venmo only)", text)
        self.assertIn("Please put Electric bill split in the payment caption", text)
        self.assertIn("send a screenshot", text)
        self.assertNotIn("{memo}", text)
        self.assertNotIn("{link}", text)


class ExtractLinkTests(unittest.TestCase):
    def test_one_unique(self):
        self.assertEqual(
            extract_unique_venmo_link_from_text(
                "Venmo: https://venmo.com/u/Alice\nmore"
            ),
            "https://venmo.com/u/alice",
        )

    def test_zero(self):
        self.assertIsNone(extract_unique_venmo_link_from_text("no link here"))

    def test_two_different(self):
        self.assertIsNone(
            extract_unique_venmo_link_from_text(
                "https://venmo.com/u/a https://venmo.com/u/b"
            )
        )

    def test_contains(self):
        self.assertTrue(
            text_contains_venmo_link(
                "pay https://venmo.com/u/alice now",
                "https://venmo.com/u/alice",
            )
        )
        self.assertFalse(
            text_contains_venmo_link(
                "pay https://venmo.com/u/bob",
                "https://venmo.com/u/alice",
            )
        )


class StoredTagTests(unittest.TestCase):
    def test_prefers_stored_tag(self):
        v = MagicMock()
        v.venmo_tag = "@stored"
        v.response_text = "Venmo: https://venmo.com/u/scraped"
        v.response_caption = None
        self.assertEqual(stored_venmo_tag(v), "@stored")

    def test_falls_back_to_text(self):
        v = MagicMock()
        v.venmo_tag = None
        v.response_text = "Venmo: https://venmo.com/u/scraped"
        v.response_caption = None
        self.assertEqual(stored_venmo_tag(v), "@scraped")


class ApplyVenmoVariantFieldsApiTests(unittest.TestCase):
    def test_rejects_mismatched_tag_link(self):
        from fastapi import HTTPException

        from api.routes.v2_payment import _apply_venmo_variant_fields

        method = MagicMock()
        method.slug = "venmo"
        with self.assertRaises(HTTPException) as ctx:
            _apply_venmo_variant_fields(
                method,
                {
                    "label": "A",
                    "venmo_tag": "@alice",
                    "venmo_link": "https://venmo.com/u/bob",
                    "venmo_response_mode": "default",
                },
                creating=True,
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("same account", ctx.exception.detail)

    def test_create_defaults_mode(self):
        from api.routes.v2_payment import _apply_venmo_variant_fields

        method = MagicMock()
        method.slug = "venmo"
        out = _apply_venmo_variant_fields(
            method,
            {
                "label": "A",
                "venmo_tag": "@alice",
                "venmo_link": "https://venmo.com/u/alice",
            },
            creating=True,
        )
        self.assertEqual(out["venmo_response_mode"], "default")
        self.assertEqual(out["response_type"], "text")
        self.assertEqual(out["venmo_tag"], "@alice")

    def test_non_venmo_clears_fields(self):
        from api.routes.v2_payment import _apply_venmo_variant_fields

        method = MagicMock()
        method.slug = "zelle"
        out = _apply_venmo_variant_fields(
            method,
            {
                "label": "A",
                "venmo_tag": "@alice",
                "venmo_link": "https://venmo.com/u/alice",
                "venmo_response_mode": "default",
            },
            creating=True,
        )
        self.assertIsNone(out["venmo_tag"])
        self.assertIsNone(out["venmo_link"])
        self.assertIsNone(out["venmo_response_mode"])


class FirstTimeDestinationLinkTests(unittest.TestCase):
    def test_uses_venmo_link_when_text_empty(self):
        text = format_first_time_payment_destination_message(
            payment_method_slug="venmo",
            variant_response_text="",
            venmo_link="https://venmo.com/u/club-round",
        )
        self.assertIn("venmo.com/u/club-round", text)
        self.assertIn("SCREENSHOT", text)


if __name__ == "__main__":
    unittest.main()
