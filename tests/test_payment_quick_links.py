"""Payment dashboard quick-access links."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import ROLE_ACCOUNT_MANAGER, ROLE_GTO, get_current_admin
from api.payment_quick_links import link_visible, list_quick_links, normalize_url
from api.routes.payment_quick_links import router
from db.connection import get_db_dependency


class VisibilityTestCase(unittest.TestCase):
    def test_all_methods_all_clubs_always_shows(self) -> None:
        self.assertTrue(
            link_visible(
                link_method=None,
                link_club_id=None,
                filter_method="all",
                filter_club_id=None,
            )
        )
        self.assertTrue(
            link_visible(
                link_method=None,
                link_club_id=None,
                filter_method="crypto",
                filter_club_id=7,
            )
        )

    def test_one_method_all_clubs(self) -> None:
        self.assertTrue(
            link_visible(
                link_method="crypto",
                link_club_id=None,
                filter_method="crypto",
                filter_club_id=None,
            )
        )
        self.assertTrue(
            link_visible(
                link_method="crypto",
                link_club_id=None,
                filter_method="crypto",
                filter_club_id=7,
            )
        )
        self.assertFalse(
            link_visible(
                link_method="crypto",
                link_club_id=None,
                filter_method="all",
                filter_club_id=None,
            )
        )
        self.assertFalse(
            link_visible(
                link_method="crypto",
                link_club_id=None,
                filter_method="venmo",
                filter_club_id=None,
            )
        )

    def test_all_methods_one_club(self) -> None:
        self.assertTrue(
            link_visible(
                link_method=None,
                link_club_id=7,
                filter_method="all",
                filter_club_id=7,
            )
        )
        self.assertTrue(
            link_visible(
                link_method=None,
                link_club_id=7,
                filter_method="crypto",
                filter_club_id=7,
            )
        )
        self.assertFalse(
            link_visible(
                link_method=None,
                link_club_id=7,
                filter_method="crypto",
                filter_club_id=None,
            )
        )

    def test_one_method_one_club(self) -> None:
        self.assertTrue(
            link_visible(
                link_method="crypto",
                link_club_id=7,
                filter_method="crypto",
                filter_club_id=7,
            )
        )
        self.assertFalse(
            link_visible(
                link_method="crypto",
                link_club_id=7,
                filter_method="crypto",
                filter_club_id=None,
            )
        )
        self.assertFalse(
            link_visible(
                link_method="crypto",
                link_club_id=7,
                filter_method="all",
                filter_club_id=7,
            )
        )


class NormalizeUrlTestCase(unittest.TestCase):
    def test_requires_http(self) -> None:
        with self.assertRaises(ValueError):
            normalize_url("javascript:alert(1)")
        with self.assertRaises(ValueError):
            normalize_url("/relative")
        self.assertEqual(normalize_url("https://example.com/x"), "https://example.com/x")


class ApiAccessTestCase(unittest.TestCase):
    def _client(self, role: str) -> TestClient:
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_admin] = lambda: role
        app.dependency_overrides[get_db_dependency] = lambda: MagicMock()
        return TestClient(app)

    def test_account_manager_cannot_create(self) -> None:
        res = self._client(ROLE_ACCOUNT_MANAGER).post(
            "/api/payments/quick-links",
            json={"title": "Docs", "url": "https://example.com"},
        )
        self.assertEqual(res.status_code, 403)

    def test_gto_cannot_create(self) -> None:
        res = self._client(ROLE_GTO).post(
            "/api/payments/quick-links",
            json={"title": "Docs", "url": "https://example.com"},
        )
        self.assertEqual(res.status_code, 403)

    def test_account_manager_cannot_delete(self) -> None:
        res = self._client(ROLE_ACCOUNT_MANAGER).delete("/api/payments/quick-links/1")
        self.assertEqual(res.status_code, 403)


class GtoListFilterTestCase(unittest.TestCase):
    def test_gto_only_sees_all_clubs_or_clubgto(self) -> None:
        db = MagicMock()
        all_clubs = MagicMock(id=1, title="A", url="https://a.test", method=None, club_id=None, sort_order=0)
        gto = MagicMock(id=2, title="B", url="https://b.test", method="crypto", club_id=7, sort_order=1)
        other = MagicMock(id=3, title="C", url="https://c.test", method=None, club_id=3, sort_order=2)
        q = MagicMock()
        q.order_by.return_value.all.return_value = [all_clubs, gto, other]
        db.query.return_value = q
        db.get.return_value = MagicMock(name="ClubGTO")

        rows = list_quick_links(db, role=ROLE_GTO, gto_club_id=7)
        self.assertEqual([r["id"] for r in rows], [1, 2])
