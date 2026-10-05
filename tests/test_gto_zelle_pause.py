"""48-hour GTO Zelle variant pause."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import ROLE_ACCOUNT_MANAGER, ROLE_ADMIN, ROLE_GTO, get_current_admin
from api.routes import gto_zelle as gto_zelle_routes
from api.routes import v2_payment as v2
from api.schemas_v2 import ClubPaymentTierVariantUpdate
from bot.services import club_payment_v2
from bot.services.variant_pause import (
    apply_pause,
    apply_resume,
    build_gto_zelle_cards,
    release_expired_variant_pauses,
)
from db.connection import get_db_dependency


def _moment() -> datetime:
    return datetime(2026, 10, 4, 18, 0, tzinfo=timezone.utc)


def _variant(**overrides):
    data = dict(
        id=1,
        label="Citizens",
        is_active=True,
        paused_until=None,
        weight=10,
        method_id=4,
        tier_id=10,
        sort_order=0,
        response_type="text",
        response_text="pay here",
        response_file_id=None,
        response_caption=None,
        use_group_checkout_link=None,
        group_checkout_provider=None,
        hyperlink_text=None,
        checkout_min_amount=None,
        checkout_max_amount=None,
        venmo_tag=None,
        venmo_link=None,
        venmo_response_mode=None,
        cashapp_tag=None,
        cashapp_link=None,
        cashapp_response_mode=None,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def _tier(label: str = "Default", tier_id: int = 10):
    return SimpleNamespace(id=tier_id, label=label, method_id=4)


class PauseRuleTests(unittest.TestCase):
    def test_pause_sets_inactive_for_48_hours(self):
        row = _variant()
        now = _moment()
        apply_pause(row, now)
        self.assertFalse(row.is_active)
        self.assertEqual(row.paused_until, now + timedelta(hours=48))

    def test_second_pause_is_rejected(self):
        row = _variant()
        now = _moment()
        apply_pause(row, now)
        with self.assertRaises(ValueError):
            apply_pause(row, now + timedelta(hours=1))

    def test_inactive_without_timer_cannot_be_paused(self):
        row = _variant(is_active=False)
        with self.assertRaises(ValueError):
            apply_pause(row, _moment())

    def test_resume_clears_timer(self):
        row = _variant()
        now = _moment()
        apply_pause(row, now)
        apply_resume(row, now + timedelta(hours=2))
        self.assertTrue(row.is_active)
        self.assertIsNone(row.paused_until)

    def test_resume_rejected_when_not_paused(self):
        with self.assertRaises(ValueError):
            apply_resume(_variant(), _moment())

    def test_future_pause_is_not_offered(self):
        row = _variant(
            is_active=True,
            paused_until=datetime.now(timezone.utc) + timedelta(hours=3),
        )
        self.assertFalse(club_payment_v2.variant_is_active(row))

    def test_release_expired_pause(self):
        row = _variant(
            is_active=False,
            paused_until=_moment() - timedelta(minutes=1),
        )
        query = MagicMock()
        query.filter.return_value = query
        query.all.return_value = [row]
        session = MagicMock()
        session.query.return_value = query
        release_expired_variant_pauses(session)
        self.assertTrue(row.is_active)
        self.assertIsNone(row.paused_until)
        session.flush.assert_called_once()

    def test_cards_include_tier(self):
        first = _variant(id=1, label="Citizens")
        second = _variant(id=2, label="Citizens")
        unique = _variant(id=3, label="Chase")
        cards = build_gto_zelle_cards(
            [
                (first, _tier("Under $100", 1)),
                (second, _tier("Over $100", 2)),
                (unique, _tier("Default", 3)),
            ],
            _moment(),
        )
        by_id = {card["id"]: card for card in cards}
        self.assertEqual(by_id[1]["tier_id"], 1)
        self.assertEqual(by_id[1]["tier_label"], "Under $100")
        self.assertEqual(by_id[2]["tier_label"], "Over $100")
        self.assertEqual(by_id[3]["tier_label"], "Default")
        self.assertEqual(by_id[1]["state"], "active")


class PickSkipsPauseTests(unittest.TestCase):
    @patch("bot.services.club_payment_v2.get_db")
    def test_pick_variant_skips_future_pause(self, mock_get_db):
        paused = _variant(
            id=1,
            is_active=True,
            paused_until=datetime.now(timezone.utc) + timedelta(hours=5),
        )
        active = _variant(id=2, weight=20)
        session = MagicMock()
        session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = [
            paused,
            active,
        ]
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False
        mock_get_db.return_value = cm

        with patch(
            "bot.services.club_payment_v2.random.choices", return_value=[active]
        ) as choices:
            result = club_payment_v2.pick_variant(4, tier_id=10)

        self.assertEqual(choices.call_args[0][0], [active])
        self.assertEqual(result["variant_id"], 2)


class AdminSaveClearsTimerTests(unittest.TestCase):
    def _save(self, variant, body: ClubPaymentTierVariantUpdate):
        tier = _tier()
        method = SimpleNamespace(id=4, slug="zelle", club_id=7, tiers=[])
        with (
            patch.object(v2, "_get_variant", return_value=variant),
            patch.object(v2, "_get_method", return_value=method),
            patch.object(v2, "_get_tier", return_value=tier),
            patch("bot.services.variant_pause.release_expired_variant_pauses"),
        ):
            return v2.update_variant(variant.id, body, MagicMock())

    def test_inactive_save_clears_timer(self):
        future = _moment() + timedelta(hours=10)
        variant = _variant(is_active=False, paused_until=future)
        self._save(variant, ClubPaymentTierVariantUpdate(is_active=False))
        self.assertFalse(variant.is_active)
        self.assertIsNone(variant.paused_until)

    def test_active_save_clears_timer(self):
        future = _moment() + timedelta(hours=10)
        variant = _variant(is_active=False, paused_until=future)
        self._save(variant, ClubPaymentTierVariantUpdate(is_active=True))
        self.assertTrue(variant.is_active)
        self.assertIsNone(variant.paused_until)

    def test_other_fields_leave_timer(self):
        future = _moment() + timedelta(hours=10)
        variant = _variant(is_active=False, paused_until=future)
        self._save(variant, ClubPaymentTierVariantUpdate(weight=7))
        self.assertEqual(variant.paused_until, future)
        self.assertEqual(variant.weight, 7)
        self.assertFalse(variant.is_active)


class GtoZelleRoleTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(gto_zelle_routes.router)
        self.app.dependency_overrides[get_db_dependency] = lambda: MagicMock()

    def _client(self, role: str) -> TestClient:
        self.app.dependency_overrides[get_current_admin] = lambda: role
        return TestClient(self.app)

    def test_admin_and_gto_are_forbidden(self):
        for role in (ROLE_ADMIN, ROLE_GTO):
            client = self._client(role)
            self.assertEqual(client.get("/api/v2/gto-zelle").status_code, 403)
            self.assertEqual(client.post("/api/v2/gto-zelle/1/pause").status_code, 403)
            self.assertEqual(client.post("/api/v2/gto-zelle/1/resume").status_code, 403)

    def test_account_manager_can_list(self):
        card = {
            "id": 1,
            "label": "Citizens",
            "tier_id": 10,
            "tier_label": "Default",
            "state": "active",
            "paused_until": None,
        }
        with patch("api.routes.gto_zelle.list_gto_zelle_cards", return_value=[card]):
            response = self._client(ROLE_ACCOUNT_MANAGER).get("/api/v2/gto-zelle")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["label"], "Citizens")

    def test_pause_rejects_inactive_variant(self):
        with patch(
            "api.routes.gto_zelle.pause_gto_zelle_variant",
            side_effect=ValueError("Variant is not active"),
        ):
            response = self._client(ROLE_ACCOUNT_MANAGER).post(
                "/api/v2/gto-zelle/1/pause"
            )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
