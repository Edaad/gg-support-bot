"""Shared destination tag on deposit variants."""

import unittest
from unittest.mock import MagicMock

from fastapi import HTTPException

from api.routes.v2_payment import _apply_destination_fields
from bot.services.destination_fields import validate_zelle_tag
from bot.services.venmo_variant_fields import stored_venmo_tag


class ValidateZelleTagTests(unittest.TestCase):
    def test_email(self):
        self.assertEqual(validate_zelle_tag("Pay@Example.com"), "pay@example.com")

    def test_phone(self):
        self.assertEqual(validate_zelle_tag("(310) 567-0961"), "3105670961")

    def test_rejects_short_phone(self):
        with self.assertRaises(ValueError):
            validate_zelle_tag("555-1234")


class ApplyDestinationFieldsTests(unittest.TestCase):
    def test_venmo_tag_key_mirrors_legacy_columns(self):
        method = MagicMock()
        method.slug = "venmo"
        out = _apply_destination_fields(
            method,
            {
                "label": "A",
                "tag": "@Alice",
                "link": "https://venmo.com/u/Alice",
            },
            tier=None,
            creating=True,
        )
        self.assertEqual(out["tag"], "@alice")
        self.assertEqual(out["link"], "https://venmo.com/u/alice")
        self.assertEqual(out["response_mode"], "default")
        self.assertEqual(out["venmo_tag"], "@alice")
        self.assertIsNone(out["cashapp_tag"])

    def test_legacy_venmo_keys_still_fill_tag(self):
        method = MagicMock()
        method.slug = "venmo"
        out = _apply_destination_fields(
            method,
            {
                "label": "A",
                "venmo_tag": "@alice",
                "venmo_link": "https://venmo.com/u/alice",
                "venmo_response_mode": "text",
            },
            tier=None,
            creating=True,
        )
        self.assertEqual(out["tag"], "@alice")
        self.assertEqual(out["response_mode"], "text")

    def test_zelle_stores_email_and_clears_other_rails(self):
        method = MagicMock()
        method.slug = "zelle"
        out = _apply_destination_fields(
            method,
            {"label": "A", "tag": "clubgto1234@gmail.com"},
            tier=None,
            creating=True,
        )
        self.assertEqual(out["tag"], "clubgto1234@gmail.com")
        self.assertIsNone(out["link"])
        self.assertIsNone(out["response_mode"])
        self.assertIsNone(out["venmo_tag"])
        self.assertIsNone(out["cashapp_tag"])

    def test_zelle_create_without_tag_rejected(self):
        method = MagicMock()
        method.slug = "zelle"
        with self.assertRaises(HTTPException) as ctx:
            _apply_destination_fields(method, {"label": "A"}, tier=None, creating=True)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_zelle_update_without_tag_keeps_legacy_row(self):
        method = MagicMock()
        method.slug = "zelle"
        existing = MagicMock()
        existing.tag = None
        out = _apply_destination_fields(
            method,
            {"label": "Renamed"},
            tier=None,
            existing=existing,
            creating=False,
        )
        self.assertIsNone(out["tag"])


class StoredTagPreferenceTests(unittest.TestCase):
    def test_shared_tag_wins_over_venmo_column(self):
        variant = MagicMock()
        variant.tag = "@shared"
        variant.venmo_tag = "@legacy"
        variant.response_text = None
        variant.response_caption = None
        self.assertEqual(stored_venmo_tag(variant), "@shared")


if __name__ == "__main__":
    unittest.main()
