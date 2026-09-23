"""Tests for support group photo backfill helpers."""

from __future__ import annotations

import unittest

from bot.services.group_photo_backfill import classify_group_photo_action


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


if __name__ == "__main__":
    unittest.main()
