"""Tests for Cash App variant destination fields and default template."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from bot.services.cashapp_variant_fields import (
    build_default_cashapp_response_text,
    extract_unique_cashapp_link_from_text,
    stored_cashapp_tag,
    text_contains_cashapp_link,
    validate_cashapp_link,
    validate_cashapp_response_mode,
    validate_cashapp_tag,
    validate_cashapp_tag_and_link,
)
from bot.services.payment_method_binding import (
    format_first_time_payment_destination_message,
)
from bot.services.venmo_variant_fields import MEMO_HIGH, pick_venmo_memo


class ValidateCashappFieldsTests(unittest.TestCase):
    def test_tag_accepts_cashtag(self):
        self.assertEqual(validate_cashapp_tag("$EduardoK4444"), "$eduardok4444")

    def test_tag_adds_dollar(self):
        self.assertEqual(validate_cashapp_tag("club-round"), "$club-round")

    def test_tag_rejects_bad(self):
        with self.assertRaises(ValueError):
            validate_cashapp_tag("$")
        with self.assertRaises(ValueError):
            validate_cashapp_tag("$bad space")

    def test_link_accepts_canonical(self):
        self.assertEqual(
            validate_cashapp_link("https://Cash.app/$EduardoK4444"),
            "https://cash.app/$eduardok4444",
        )

    def test_link_rejects_www_query_slash(self):
        with self.assertRaises(ValueError):
            validate_cashapp_link("https://www.cash.app/$x")
        with self.assertRaises(ValueError):
            validate_cashapp_link("https://cash.app/$x?foo=1")
        with self.assertRaises(ValueError):
            validate_cashapp_link("https://cash.app/$x/")
        with self.assertRaises(ValueError):
            validate_cashapp_link("http://cash.app/$x")

    def test_tag_link_must_match(self):
        with self.assertRaises(ValueError) as ctx:
            validate_cashapp_tag_and_link("$alice", "https://cash.app/$bob")
        self.assertIn("same account", str(ctx.exception))

    def test_tag_link_ok(self):
        tag, link = validate_cashapp_tag_and_link("$Alice", "https://cash.app/$Alice")
        self.assertEqual(tag, "$alice")
        self.assertEqual(link, "https://cash.app/$alice")

    def test_mode(self):
        self.assertEqual(validate_cashapp_response_mode("Default"), "default")
        with self.assertRaises(ValueError):
            validate_cashapp_response_mode("email")


class DefaultTemplateTests(unittest.TestCase):
    def test_wording(self):
        text = build_default_cashapp_response_text(
            "https://cash.app/$eduardok4444",
            200,
            memo="Electric bill split",
        )
        self.assertIn("Cash App: https://cash.app/$eduardok4444", text)
        self.assertNotIn("friends and family", text)
        self.assertIn(
            "Please put <code>Electric bill split</code> in the payment caption",
            text,
        )
        self.assertIn("Once sent, please send us a screenshot", text)
        self.assertIn("\n\n•", text)
        self.assertNotIn("{memo}", text)
        self.assertNotIn("{link}", text)

    def test_shared_memo_picker_high(self):
        self.assertEqual(pick_venmo_memo(1000), MEMO_HIGH[0])


class ExtractLinkTests(unittest.TestCase):
    def test_one_unique(self):
        self.assertEqual(
            extract_unique_cashapp_link_from_text(
                "Cashapp: https://cash.app/$eduardok4444\nmore"
            ),
            "https://cash.app/$eduardok4444",
        )

    def test_zero(self):
        self.assertIsNone(extract_unique_cashapp_link_from_text("no link here"))

    def test_two_different(self):
        self.assertIsNone(
            extract_unique_cashapp_link_from_text(
                "https://cash.app/$a https://cash.app/$b"
            )
        )

    def test_contains(self):
        self.assertTrue(
            text_contains_cashapp_link(
                "pay https://cash.app/$alice now",
                "https://cash.app/$alice",
            )
        )
        self.assertFalse(
            text_contains_cashapp_link(
                "pay https://cash.app/$bob",
                "https://cash.app/$alice",
            )
        )


class StoredTagTests(unittest.TestCase):
    def test_prefers_stored_tag(self):
        v = MagicMock()
        v.cashapp_tag = "$stored"
        v.response_text = "Cashapp: https://cash.app/$scraped"
        v.response_caption = None
        self.assertEqual(stored_cashapp_tag(v), "$stored")

    def test_falls_back_to_text(self):
        v = MagicMock()
        v.cashapp_tag = None
        v.response_text = "Cashapp: https://cash.app/$scraped"
        v.response_caption = None
        self.assertEqual(stored_cashapp_tag(v), "$scraped")


class ApplyCashappVariantFieldsApiTests(unittest.TestCase):
    def test_rejects_mismatched_tag_link(self):
        from fastapi import HTTPException

        from api.routes.v2_payment import _apply_cashapp_variant_fields

        method = MagicMock()
        method.slug = "cashapp"
        tier = MagicMock()
        tier.use_group_checkout_link = False
        with self.assertRaises(HTTPException) as ctx:
            _apply_cashapp_variant_fields(
                method,
                {
                    "label": "A",
                    "cashapp_tag": "$alice",
                    "cashapp_link": "https://cash.app/$bob",
                    "cashapp_response_mode": "default",
                    "use_group_checkout_link": False,
                },
                tier=tier,
                creating=True,
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("same account", ctx.exception.detail)

    def test_create_defaults_mode(self):
        from api.routes.v2_payment import _apply_cashapp_variant_fields

        method = MagicMock()
        method.slug = "cashapp"
        tier = MagicMock()
        tier.use_group_checkout_link = False
        out = _apply_cashapp_variant_fields(
            method,
            {
                "label": "A",
                "cashapp_tag": "$alice",
                "cashapp_link": "https://cash.app/$alice",
                "use_group_checkout_link": False,
            },
            tier=tier,
            creating=True,
        )
        self.assertEqual(out["cashapp_response_mode"], "default")
        self.assertEqual(out["response_type"], "text")
        self.assertEqual(out["cashapp_tag"], "$alice")

    def test_checkout_clears_fields(self):
        from api.routes.v2_payment import _apply_cashapp_variant_fields

        method = MagicMock()
        method.slug = "cashapp"
        tier = MagicMock()
        tier.use_group_checkout_link = True
        out = _apply_cashapp_variant_fields(
            method,
            {
                "label": "Stripe",
                "cashapp_tag": "$alice",
                "cashapp_link": "https://cash.app/$alice",
                "cashapp_response_mode": "default",
                "use_group_checkout_link": True,
            },
            tier=tier,
            creating=True,
        )
        self.assertIsNone(out["cashapp_tag"])
        self.assertIsNone(out["cashapp_link"])
        self.assertIsNone(out["cashapp_response_mode"])

    def test_non_cashapp_clears_fields(self):
        from api.routes.v2_payment import _apply_cashapp_variant_fields

        method = MagicMock()
        method.slug = "zelle"
        tier = MagicMock()
        tier.use_group_checkout_link = False
        out = _apply_cashapp_variant_fields(
            method,
            {
                "label": "A",
                "cashapp_tag": "$alice",
                "cashapp_link": "https://cash.app/$alice",
                "cashapp_response_mode": "default",
            },
            tier=tier,
            creating=True,
        )
        self.assertIsNone(out["cashapp_tag"])
        self.assertIsNone(out["cashapp_link"])
        self.assertIsNone(out["cashapp_response_mode"])


class ApplyCashappDefaultResponseTests(unittest.TestCase):
    def test_builds_html_template(self):
        from bot.handlers.deposit import _apply_cashapp_default_response

        out = _apply_cashapp_default_response(
            {
                "cashapp_response_mode": "default",
                "cashapp_link": "https://cash.app/$eduardok4444",
                "response_type": "text",
                "response_text": "ignored",
            },
            200,
        )
        self.assertEqual(out["parse_mode"], "HTML")
        self.assertIn("Cash App: https://cash.app/$eduardok4444", out["response_text"])
        self.assertIn("<code>", out["response_text"])
        self.assertNotIn("friends and family", out["response_text"])


class FirstTimeDestinationLinkTests(unittest.TestCase):
    def test_uses_cashapp_link_when_text_empty(self):
        text = format_first_time_payment_destination_message(
            payment_method_slug="cashapp",
            variant_response_text="",
            cashapp_link="https://cash.app/$eduardok4444",
        )
        self.assertIn("cash.app/$eduardok4444", text)
        self.assertIn("SCREENSHOT", text)


if __name__ == "__main__":
    unittest.main()
