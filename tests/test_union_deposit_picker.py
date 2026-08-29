"""Tests for deduplicated union-type deposit picker."""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bot.services.union_deposit_picker import (
    build_deposit_picker_methods,
    list_union_methods_for_club,
    pick_union_method,
)
from bot.services.union_method_types import UNION_METHOD_TYPES


def _union_row(*, id: int, tag: str, sort_order: int = 0, limit: str = "1000"):
    return SimpleNamespace(
        id=id,
        name="Zelle",
        slug=tag,
        sort_order=sort_order,
        deposit_limit=Decimal(limit),
        min_amount=None,
        max_amount=None,
        tracks_manual_requests=True,
        is_active=True,
        method_clubs=[],
    )


class ListUnionMethodsForClubTests(unittest.TestCase):
    @patch("bot.services.union_deposit_picker.get_db")
    def test_expunges_rows_before_return(self, mock_get_db):
        row = SimpleNamespace(id=9, name="Zelle")
        session = MagicMock()
        q = session.query.return_value
        q.join.return_value = q
        q.filter.return_value = q
        q.options.return_value = q
        q.order_by.return_value = q
        q.all.return_value = [row]
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False
        mock_get_db.return_value = cm

        result = list_union_methods_for_club(1)

        session.expunge.assert_called_once_with(row)
        self.assertEqual(result, [row])
        self.assertEqual(result[0].name, "Zelle")


class BuildDepositPickerMethodsTests(unittest.TestCase):
    @patch("bot.services.union_deposit_picker.get_methods_for_amount")
    def test_deduplicates_zelle_union_and_club(self, mock_get):
        mock_get.return_value = [
            {
                "id": 1,
                "name": "Venmo",
                "slug": "venmo",
                "tracks_manual_requests": False,
            },
            {
                "id": 2,
                "name": "Zelle",
                "slug": "zelle",
                "tracks_manual_requests": False,
            },
            {
                "id": 9,
                "name": "Zelle",
                "slug": "main",
                "tracks_manual_requests": True,
                "union_type": "zelle",
            },
        ]

        shown = build_deposit_picker_methods(1, Decimal("100"))
        names = [m["name"] for m in shown]
        self.assertEqual(names, ["Venmo", "Zelle"])
        zelle_row = next(m for m in shown if m["type_slug"] == "zelle")
        venmo_row = next(m for m in shown if m["type_slug"] == "venmo")
        self.assertEqual(zelle_row["picker_kind"], "union_type")
        self.assertEqual(venmo_row["picker_kind"], "union_type")

    @patch("bot.services.union_deposit_picker.get_methods_for_amount")
    def test_deduplicates_venmo_union_and_club(self, mock_get):
        mock_get.return_value = [
            {
                "id": 1,
                "name": "Zelle",
                "slug": "zelle",
                "tracks_manual_requests": False,
            },
            {
                "id": 2,
                "name": "Venmo",
                "slug": "venmo",
                "tracks_manual_requests": False,
            },
            {
                "id": 10,
                "name": "Venmo",
                "slug": "venmo-pool",
                "tracks_manual_requests": True,
                "union_type": "venmo",
            },
        ]

        shown = build_deposit_picker_methods(1, Decimal("100"))
        names = [m["name"] for m in shown]
        self.assertEqual(names, ["Venmo", "Zelle"])
        venmo_row = next(m for m in shown if m["name"] == "Venmo")
        self.assertEqual(venmo_row["picker_kind"], "union_type")
        self.assertEqual(venmo_row["type_slug"], "venmo")

    @patch("bot.services.union_deposit_picker.get_methods_for_amount")
    def test_shows_type_when_union_only(self, mock_get):
        mock_get.return_value = [
            {
                "id": 9,
                "name": "Zelle",
                "slug": "pool",
                "tracks_manual_requests": True,
                "union_type": "zelle",
            }
        ]

        shown = build_deposit_picker_methods(1, Decimal("500"))
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0]["type_slug"], "zelle")

    @patch("bot.services.union_deposit_picker.get_methods_for_amount")
    def test_hides_union_type_below_min(self, mock_get):
        mock_get.return_value = [
            {
                "id": 1,
                "name": "Venmo",
                "slug": "venmo",
                "tracks_manual_requests": False,
            }
        ]

        shown = build_deposit_picker_methods(1, Decimal("25"))
        names = [m["name"] for m in shown]
        self.assertEqual(names, ["Venmo"])
        self.assertFalse(any(m.get("type_slug") == "zelle" for m in shown))

    @patch("bot.services.union_deposit_picker.get_methods_for_amount")
    def test_hides_union_type_when_filtered_out(self, mock_get):
        mock_get.return_value = []

        shown = build_deposit_picker_methods(1, Decimal("25"))
        self.assertEqual(shown, [])


class PickUnionMethodTests(unittest.TestCase):
    @patch("bot.services.union_deposit_picker.get_db")
    @patch("bot.services.union_deposit_picker.capacity_allows")
    @patch("bot.services.union_deposit_picker.list_union_methods_for_club")
    def test_respects_sort_order(self, mock_list, mock_capacity, mock_get_db):
        first = _union_row(id=1, tag="a", sort_order=0, limit="1000")
        second = _union_row(id=2, tag="b", sort_order=1, limit="1000")
        mock_list.return_value = [first, second]
        session = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False
        mock_get_db.return_value = cm

        def capacity_side_effect(session, *, method_id, amount, deposit_limit):
            return int(method_id) == 2

        mock_capacity.side_effect = capacity_side_effect

        picked = pick_union_method(1, "zelle", Decimal("600"))
        self.assertIsNotNone(picked)
        self.assertEqual(int(picked.id), 2)

    @patch("bot.services.union_deposit_picker.get_db")
    @patch("bot.services.union_deposit_picker.capacity_allows")
    @patch("bot.services.union_deposit_picker.list_union_methods_for_club")
    def test_returns_none_when_all_full(self, mock_list, mock_capacity, mock_get_db):
        mock_list.return_value = [_union_row(id=1, tag="a")]
        session = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False
        mock_get_db.return_value = cm
        mock_capacity.return_value = False
        self.assertIsNone(pick_union_method(1, "zelle", Decimal("600")))


class UnionMethodTypesTests(unittest.TestCase):
    def test_club_slug_map(self):
        self.assertEqual(UNION_METHOD_TYPES["zelle"]["club_slug"], "zelle")
        self.assertEqual(UNION_METHOD_TYPES["cashapp"]["club_slug"], "cashapp")
        self.assertEqual(UNION_METHOD_TYPES["venmo"]["club_slug"], "venmo")
        self.assertEqual(UNION_METHOD_TYPES["venmo"]["name"], "Venmo")


class EnsureUniqueTagTests(unittest.TestCase):
    def test_collision_appends_suffix(self):
        from api.routes.union_methods import _ensure_unique_internal_identifier

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [object(), None]
        tag = _ensure_unique_internal_identifier(db, "main")
        self.assertTrue(tag.startswith("main-"))
        self.assertNotEqual(tag, "main")


if __name__ == "__main__":
    unittest.main()
