"""Unit tests for per-group deposit method access visibility and menus."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bot.services.deposit_method_access import (
    filter_cashout_methods_for_chat,
    filter_deposit_methods_for_chat,
    format_access_list,
    is_cashout_method_allowed_for_chat,
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
        session._method_q = method_q
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

    def test_cashout_direction_passed_to_query(self):
        session = self._session([])
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = session
        mock_cm.__exit__.return_value = False
        with patch(
            "bot.services.deposit_method_access.get_db", return_value=mock_cm
        ):
            methods_for_action(10, -100, "blacklist", direction="cashout")
        session._method_q.filter_by.assert_called_with(
            club_id=10, direction="cashout", is_active=True
        )

    def test_cashout_whitelist_includes_public_crypto(self):
        orig = self._methods
        self._methods = lambda: [
            SimpleNamespace(
                id=1, name="Zelle", slug="zelle", is_public=True, sort_order=0
            ),
            SimpleNamespace(
                id=4, name="Crypto", slug="crypto", is_public=True, sort_order=3
            ),
        ]
        try:
            session = self._session([])
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value = session
            mock_cm.__exit__.return_value = False
            with patch(
                "bot.services.deposit_method_access.get_db", return_value=mock_cm
            ):
                cashout = methods_for_action(
                    10, -100, "whitelist", direction="cashout"
                )
            session2 = self._session([])
            mock_cm2 = MagicMock()
            mock_cm2.__enter__.return_value = session2
            mock_cm2.__exit__.return_value = False
            with patch(
                "bot.services.deposit_method_access.get_db", return_value=mock_cm2
            ):
                deposit = methods_for_action(
                    10, -100, "whitelist", direction="deposit"
                )
        finally:
            self._methods = orig
        self.assertEqual([m["slug"] for m in cashout], ["crypto"])
        self.assertEqual(deposit, [])


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

    def test_cashout_direction_allowed(self):
        from bot.services import deposit_method_access as dma

        existing = MagicMock()
        existing.id = 99
        existing.access_type = "whitelist"
        method = MagicMock()
        method.club_id = 5
        method.direction = "cashout"
        method.name = "Crypto"
        method.slug = "crypto"

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
                club_payment_method_id=9,
                access_type="blacklist",
                direction="cashout",
            )

        self.assertEqual(existing.access_type, "blacklist")
        self.assertEqual(entry.method_slug, "crypto")
        session.add.assert_not_called()

    def test_rejects_cross_direction(self):
        from bot.services import deposit_method_access as dma

        method = MagicMock()
        method.club_id = 5
        method.direction = "cashout"
        method.name = "Crypto"
        method.slug = "crypto"

        session = MagicMock()
        session.query.return_value.get.return_value = method

        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = session
        mock_cm.__exit__.return_value = False

        with patch.object(dma, "get_db", return_value=mock_cm):
            with self.assertRaises(ValueError) as ctx:
                dma.upsert_access(
                    telegram_chat_id=-100,
                    club_id=5,
                    club_payment_method_id=9,
                    access_type="blacklist",
                )
        self.assertIn("deposit", str(ctx.exception))


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
        self.assertIn("zelle", text)
        self.assertNotIn("Zelle", text)

    def test_cashout_empty(self):
        self.assertIn("No cashout method", format_access_list([], direction="cashout"))


class TestListAccessEntriesDirection(unittest.TestCase):
    def test_filters_by_direction(self):
        from bot.services import deposit_method_access as dma

        access = SimpleNamespace(
            id=1,
            telegram_chat_id=-100,
            club_id=5,
            club_payment_method_id=9,
            access_type="blacklist",
        )
        method = SimpleNamespace(name="Crypto", slug="crypto")
        q = MagicMock()
        q.order_by.return_value.all.return_value = [(access, method)]
        session = MagicMock()
        session.query.return_value.join.return_value.filter.return_value = q

        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = session
        mock_cm.__exit__.return_value = False

        with (
            patch.object(dma, "ADMIN_USER_IDS", {42}),
            patch.object(dma, "get_db", return_value=mock_cm),
            patch.object(dma, "get_group_title_for_chat", return_value=("RT / 1 / A", 5)),
        ):
            rows = dma.list_access_entries(42, direction="cashout")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].method_slug, "crypto")
        session.query.return_value.join.return_value.filter.assert_called()


class TestFilterCashoutMethodsForChat(unittest.TestCase):
    methods = [
        {"id": 1, "name": "Zelle", "slug": "zelle", "is_public": True},
        {"id": 2, "name": "Crypto", "slug": "crypto", "is_public": True},
    ]

    @patch(
        "bot.services.crypto_payments.chat_has_bound_crypto_deposit",
        return_value=False,
    )
    @patch(
        "bot.services.deposit_method_access.filter_deposit_methods_for_chat",
        side_effect=lambda _c, ms: list(ms),
    )
    def test_no_bound_payment_hides_crypto(self, _filt, _bound):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.all.return_value = []
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = session
        mock_cm.__exit__.return_value = False
        with patch(
            "bot.services.deposit_method_access.get_db", return_value=mock_cm
        ):
            result = filter_cashout_methods_for_chat(-100, self.methods)
        self.assertEqual([m["slug"] for m in result], ["zelle"])

    @patch(
        "bot.services.crypto_payments.chat_has_bound_crypto_deposit",
        return_value=True,
    )
    @patch(
        "bot.services.deposit_method_access.filter_deposit_methods_for_chat",
        side_effect=lambda _c, ms: list(ms),
    )
    def test_bound_payment_shows_crypto(self, _filt, _bound):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.all.return_value = []
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = session
        mock_cm.__exit__.return_value = False
        with patch(
            "bot.services.deposit_method_access.get_db", return_value=mock_cm
        ):
            result = filter_cashout_methods_for_chat(-100, self.methods)
        self.assertEqual([m["slug"] for m in result], ["zelle", "crypto"])

    @patch(
        "bot.services.crypto_payments.chat_has_bound_crypto_deposit",
        return_value=False,
    )
    @patch(
        "bot.services.deposit_method_access.filter_deposit_methods_for_chat",
        side_effect=lambda _c, ms: list(ms),
    )
    def test_whitelist_unlocks_crypto_without_bound_payment(self, _filt, _bound):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.all.return_value = [
            (2, "whitelist")
        ]
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = session
        mock_cm.__exit__.return_value = False
        with patch(
            "bot.services.deposit_method_access.get_db", return_value=mock_cm
        ):
            result = filter_cashout_methods_for_chat(-100, self.methods)
        self.assertEqual([m["slug"] for m in result], ["zelle", "crypto"])
        _bound.assert_not_called()


class TestIsCashoutMethodAllowedForChat(unittest.TestCase):
    def _session(self, method, access_rows=()):
        session = MagicMock()
        session.query.return_value.get.return_value = method
        session.query.return_value.filter_by.return_value.all.return_value = list(
            access_rows
        )
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = session
        mock_cm.__exit__.return_value = False
        return mock_cm

    def test_public_crypto_hidden_without_bound_payment(self):
        method = SimpleNamespace(
            direction="cashout",
            is_active=True,
            is_public=True,
            slug="crypto",
        )
        with (
            patch(
                "bot.services.deposit_method_access.get_db",
                return_value=self._session(method),
            ),
            patch(
                "bot.services.crypto_payments.chat_has_bound_crypto_deposit",
                return_value=False,
            ),
        ):
            self.assertFalse(is_cashout_method_allowed_for_chat(-100, 2))

    def test_whitelist_unlocks_crypto_without_bound_payment(self):
        method = SimpleNamespace(
            direction="cashout",
            is_active=True,
            is_public=True,
            slug="crypto",
        )
        with (
            patch(
                "bot.services.deposit_method_access.get_db",
                return_value=self._session(method, [(2, "whitelist")]),
            ),
            patch(
                "bot.services.crypto_payments.chat_has_bound_crypto_deposit",
                return_value=False,
            ) as bound,
        ):
            self.assertTrue(is_cashout_method_allowed_for_chat(-100, 2))
            bound.assert_not_called()

    def test_public_crypto_shown_with_bound_payment(self):
        method = SimpleNamespace(
            direction="cashout",
            is_active=True,
            is_public=True,
            slug="crypto",
        )
        with (
            patch(
                "bot.services.deposit_method_access.get_db",
                return_value=self._session(method),
            ),
            patch(
                "bot.services.crypto_payments.chat_has_bound_crypto_deposit",
                return_value=True,
            ),
        ):
            self.assertTrue(is_cashout_method_allowed_for_chat(-100, 2))

    def test_blacklist_hides_crypto_even_with_bound_payment(self):
        method = SimpleNamespace(
            direction="cashout",
            is_active=True,
            is_public=True,
            slug="crypto",
        )
        with (
            patch(
                "bot.services.deposit_method_access.get_db",
                return_value=self._session(method, [(2, "blacklist")]),
            ),
            patch(
                "bot.services.crypto_payments.chat_has_bound_crypto_deposit",
                return_value=True,
            ) as bound,
        ):
            self.assertFalse(is_cashout_method_allowed_for_chat(-100, 2))
            bound.assert_not_called()

    def test_zelle_not_gated_by_crypto(self):
        method = SimpleNamespace(
            direction="cashout",
            is_active=True,
            is_public=True,
            slug="zelle",
        )
        with (
            patch(
                "bot.services.deposit_method_access.get_db",
                return_value=self._session(method),
            ),
            patch(
                "bot.services.crypto_payments.chat_has_bound_crypto_deposit",
                return_value=False,
            ) as bound,
        ):
            self.assertTrue(is_cashout_method_allowed_for_chat(-100, 1))
            bound.assert_not_called()


class TestChatHasBoundCryptoDeposit(unittest.TestCase):
    def test_true_when_row_exists(self):
        from bot.services.crypto_payments import chat_has_bound_crypto_deposit

        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = (1,)
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = session
        mock_cm.__exit__.return_value = False
        with patch(
            "bot.services.crypto_payments.get_db", return_value=mock_cm
        ):
            self.assertTrue(chat_has_bound_crypto_deposit(-100))

    def test_false_when_missing(self):
        from bot.services.crypto_payments import chat_has_bound_crypto_deposit

        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = session
        mock_cm.__exit__.return_value = False
        with patch(
            "bot.services.crypto_payments.get_db", return_value=mock_cm
        ):
            self.assertFalse(chat_has_bound_crypto_deposit(-100))
        exprs = session.query.return_value.filter.call_args[0]
        self.assertEqual(len(exprs), 2)
        self.assertIn("is_test", str(exprs[1]))

    def test_fail_closed_on_error(self):
        from bot.services.crypto_payments import chat_has_bound_crypto_deposit

        mock_cm = MagicMock()
        mock_cm.__enter__.side_effect = RuntimeError("db down")
        with patch(
            "bot.services.crypto_payments.get_db", return_value=mock_cm
        ):
            self.assertFalse(chat_has_bound_crypto_deposit(-100))


if __name__ == "__main__":
    unittest.main()
