"""Tests for support group photo backfill helpers."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from bot.services.group_photo_backfill import classify_group_photo_action
from scripts.backfill_support_group_photos import _lookup_entity


class TestClassifyGroupPhotoAction(unittest.TestCase):
    def test_not_in_group(self) -> None:
        self.assertEqual(
            classify_group_photo_action(entity_found=False, has_photo=False),
            "admin_not_in_group",
        )

    def test_already_has_photo(self) -> None:
        self.assertEqual(
            classify_group_photo_action(entity_found=True, has_photo=True),
            "already_has_photo",
        )

    def test_needs_photo(self) -> None:
        self.assertEqual(
            classify_group_photo_action(entity_found=True, has_photo=False),
            "needs_photo",
        )


class TestLookupEntity(unittest.TestCase):
    def test_matches_bot_api_and_legacy_ids(self) -> None:
        entity = MagicMock()
        mapping = {-1001234567890: entity}
        self.assertIs(entity, _lookup_entity(mapping, -1001234567890))
        self.assertIs(entity, _lookup_entity(mapping, -1234567890))

    def test_missing(self) -> None:
        self.assertIsNone(_lookup_entity({}, -1001))


if __name__ == "__main__":
    unittest.main()
