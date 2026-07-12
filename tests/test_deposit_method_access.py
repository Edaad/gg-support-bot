"""Unit tests for per-group deposit method access visibility and menus."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bot.services.deposit_method_access import (
    filter_deposit_methods_for_chat,
    format_access_list,
    method_visible_for_chat,
    methods_for_action,
)


class TestMethodVisibleForChat(unittest.TestCase):
    def test_public_no_access_shown(self):
        self.assertTrue(method_visible_for_chat(is_public=True, access_type=None))

    def test_public_blacklisted_hidden(self):
        self.assertFalse(
            method_visible_for_chat(is_public=True, access_type="blacklist")
        )

    def test_public_whitelisted_still_shown(self):
        # Whitelist on a public method is a no-op for visibility.
        self.assertTrue(
            method_visible_for_chat(is_public=True, access_type="whitelist")
        )

    def test_private_no_access_hidden(self):
        self.assertFalse(method_visible_for_chat(is_public=False, access_type=None))

    def test_private_whitelisted_shown(self):
        self.assertTrue(
            method_visible_for_chat(is_public=False, access_type="whitelist")
        )

    def test_private_blacklisted_hidden(self):
        # Blacklist wins even on private methods.
        self.assertFalse(
            method_visible_for_chat(is_public=False, access_type="blacklist")
        )


class TestFilterDepositMethodsForChat(unittest.TestCase):
    def test_filters_using_is_public_on_dict(self):
        methods = [
            {"id": 1, "name": "Zelle", "slug": "zelle", "is_public": True},
            {"id": 2, "name": "Wire", "slug": "wire", "is_public": False},
            {"id": 3, "name": "Venmo", "slug": "venmo", "is_public": True},
        ]
        session = MagicMock()
        session.query.return_value.filter_by.return_value.all.return_value = [
            (1, "blacklist"),
            (2, "whitelist"),
        ]
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = session
        mock_cm.__exit__.return_value = False

        with patch(
            "bot.services.deposit_method_access.get_db", return_value=mock_cm
        ):
            result = filter_deposit_methods_for_chat(-100, methods)

        slugs = [m["slug"] for m in result]
        self.assertEqual(slugs, ["wire", "venmo"])


class TestMethodsForAction(unittest.TestCase):
    def _methods(self):
        return [
            SimpleNamespace(
                id=1, name="Zelle", slug="zelle", is_public=True, sort_order=0
            ),
            SimpleNamespace(
                id=2, name="Venmo", slug="venmo", is_public=True, sort_order=1
            ),
            SimpleNamespace(
                id=3, name="Wire", slug="wire", is_public=False, sort_order=2
            ),
            SimpleNamespace(
                id=4, name="Crypto", slug="crypto", is_public=False, sort_order=3
            ),
        ]

    def _session(self, access_rows):
        session = MagicMock()
        method_q = MagicMock()
        method_q.filter_by.return_value.order_by.return_value.all.return_value = (
            self._methods()
        )
        access_q = MagicMock()
        access_q.filter_by.return_value.all.return_value = access_rows

        calls = {"n": 0}

        def query(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                return method_q
            return access_q

        session.query.side_effect = query
        return session

    def test_blacklist_menu(self):
        # Already blacklisted zelle; private methods excluded.
        session = self._session([(1, "blacklist"), (3, "whitelist")])
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = session
        mock_cm.__exit__.return_value = False
        with patch(
            "bot.services.deposit_method_access.get_db", return_value=mock_cm
        ):
            result = methods_for_action(10, -100, "blacklist")
        self.assertEqual([m["slug"] for m in result], ["venmo"])

    def test_whitelist_menu(self):
        session = self._session([(1, "blacklist"), (3, "whitelist")])
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = session
        mock_cm.__exit__.return_value = False
        with patch(
            "bot.services.deposit_method_access.get_db", return_value=mock_cm
        ):
            result = methods_for_action(10, -100, "whitelist")
        self.assertEqual([m["slug"] for m in result], ["crypto"])

    def test_remove_menu(self):
        session = self._session([(1, "blacklist"), (3, "whitelist")])
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = session
        mock_cm.__exit__.return_value = False
        with patch(
            "bot.services.deposit_method_access.get_db", return_value=mock_cm
        ):
            result = methods_for_action(10, -100, "remove")
        self.assertEqual(
            {(m["slug"], m["access_type"]) for m in result},
            {("zelle", "blacklist"), ("wire", "whitelist")},
        )


class TestUpsertAccessReplacesType(unittest.TestCase):
    def test_existing_row_updates_type(self):
        from bot.services import deposit_method_access as dma

        existing = MagicMock()
        existing.id = 99
        existing.access_type = "whitelist"
        method = MagicMock()
        method.club_id = 5
        method.direction = "deposit"
        method.name = "Zelle"
        method.slug = "zelle"

        session = MagicMock()
        session.query.return_value.get.return_value = method
        session.query.return_value.filter_by.return_value.first.return_value = existing

        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = session
        mock_cm.__exit__.return_value = False

        with (
            patch.object(dma, "get_db", return_value=mock_cm),
            patch.object(dma, "get_group_title_for_chat", return_value=("RT / 1 / A", 5)),
        ):
            entry = dma.upsert_access(
                telegram_chat_id=-100,
                club_id=5,
                club_payment_method_id=1,
                access_type="blacklist",
                created_by_telegram_user_id=42,
            )

        self.assertEqual(existing.access_type, "blacklist")
        self.assertEqual(entry.access_type, "blacklist")
        self.assertEqual(entry.method_name, "Zelle")
        session.add.assert_not_called()


class TestFormatAccessList(unittest.TestCase):
    def test_empty(self):
        self.assertIn("No deposit method", format_access_list([]))

    def test_rows(self):
        from bot.services.deposit_method_access import AccessEntry

        text = format_access_list(
            [
                AccessEntry(
                    id=1,
                    telegram_chat_id=-1,
                    club_id=1,
                    club_payment_method_id=2,
                    access_type="blacklist",
                    method_name="Zelle",
                    method_slug="zelle",
                    group_title="RT / 1 / Bob",
                )
            ]
        )
        self.assertIn("RT / 1 / Bob", text)
        self.assertIn("blacklist", text)
        self.assertIn("Zelle", text)


if __name__ == "__main__":
    unittest.main()
