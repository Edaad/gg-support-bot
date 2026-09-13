"""GTO role: payments/pool-pay denial and ClubGTO scoping on cashouts/bonuses."""

from __future__ import annotations

import os
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import (
    ROLE_ACCOUNT_MANAGER,
    ROLE_ADMIN,
    ROLE_GTO,
    get_current_admin,
)
from api.gto_club import GTO_CLUB_NAME
from api.routes import bonus as bonus_routes
from api.routes import cashout_records as cashout_routes
from api.routes import manual_deposit_requests as mdr_routes
from api.routes import payments as payments_routes
from db.connection import get_db_dependency


def _gto_club(club_id: int = 7) -> MagicMock:
    club = MagicMock()
    club.id = club_id
    club.name = GTO_CLUB_NAME
    return club


def _db_with_gto(club: MagicMock | None) -> MagicMock:
    db = MagicMock()
    q = MagicMock()
    if club is None:
        q.filter.return_value.first.return_value = None
    else:
        q.filter.return_value.first.return_value = club
    db.query.return_value = q
    return db


def _override_db(db: MagicMock):
    def _gen():
        yield db

    return _gen


class PaymentsGtoAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(os.environ, {"DASHBOARD_PASSWORD": "changeme"}, clear=False)
        self.env_patch.start()
        self.app = FastAPI()
        self.app.include_router(payments_routes.router)
        self.app.dependency_overrides[get_db_dependency] = _override_db(MagicMock())

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.app.dependency_overrides.clear()

    def test_gto_forbidden(self) -> None:
        self.app.dependency_overrides[get_current_admin] = lambda: ROLE_GTO
        client = TestClient(self.app)
        resp = client.get("/api/payments/providers")
        self.assertEqual(resp.status_code, 403)

    def test_am_allowed(self) -> None:
        self.app.dependency_overrides[get_current_admin] = lambda: ROLE_ACCOUNT_MANAGER
        client = TestClient(self.app)
        resp = client.get("/api/payments/providers")
        self.assertEqual(resp.status_code, 200)


class PoolPayRoleAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(os.environ, {"DASHBOARD_PASSWORD": "changeme"}, clear=False)
        self.env_patch.start()
        self.app = FastAPI()
        self.app.include_router(mdr_routes.router)
        self.app.dependency_overrides[get_db_dependency] = _override_db(MagicMock())

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.app.dependency_overrides.clear()

    def test_admin_allowed_structure(self) -> None:
        """Admin clears require_admin; list may still fail without full query mocks."""
        self.app.dependency_overrides[get_current_admin] = lambda: ROLE_ADMIN
        with patch("api.routes.manual_deposit_requests._list_query") as mock_lq:
            query = MagicMock()
            mock_lq.return_value = query
            summary = MagicMock(total_count=0, total_amount=Decimal("0"))
            query.order_by.return_value.enable_eagerloads.return_value.with_entities.return_value.one.return_value = (
                summary
            )
            query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
            client = TestClient(self.app)
            resp = client.get("/api/manual-deposit-requests")
        self.assertEqual(resp.status_code, 200)

    def test_am_forbidden(self) -> None:
        self.app.dependency_overrides[get_current_admin] = lambda: ROLE_ACCOUNT_MANAGER
        client = TestClient(self.app)
        resp = client.get("/api/manual-deposit-requests")
        self.assertEqual(resp.status_code, 403)

    def test_gto_forbidden(self) -> None:
        self.app.dependency_overrides[get_current_admin] = lambda: ROLE_GTO
        client = TestClient(self.app)
        resp = client.get("/api/manual-deposit-requests")
        self.assertEqual(resp.status_code, 403)


class CashoutGtoScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(os.environ, {"DASHBOARD_PASSWORD": "changeme"}, clear=False)
        self.env_patch.start()
        self.gto = _gto_club(7)
        self.db = _db_with_gto(self.gto)
        self.app = FastAPI()
        self.app.include_router(cashout_routes.router)
        self.app.dependency_overrides[get_current_admin] = lambda: ROLE_GTO
        self.app.dependency_overrides[get_db_dependency] = _override_db(self.db)

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.app.dependency_overrides.clear()

    def test_list_forces_clubgto(self) -> None:
        with patch(
            "api.routes.cashout_records.list_staff_cashout_records",
            return_value=([], 0),
        ) as mock_list, patch(
            "api.routes.cashout_records._club_name_map",
            return_value={7: GTO_CLUB_NAME},
        ):
            client = TestClient(self.app)
            resp = client.get("/api/cashout-records")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_list.call_args.kwargs["club_id"], 7)

    def test_list_other_club_forbidden(self) -> None:
        client = TestClient(self.app)
        resp = client.get("/api/cashout-records?club_id=2")
        self.assertEqual(resp.status_code, 403)

    def test_list_empty_when_clubgto_missing(self) -> None:
        self.db = _db_with_gto(None)
        self.app.dependency_overrides[get_db_dependency] = _override_db(self.db)
        client = TestClient(self.app)
        resp = client.get("/api/cashout-records")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["items"], [])
        self.assertEqual(resp.json()["total"], 0)

    def test_create_other_club_forbidden(self) -> None:
        client = TestClient(self.app)
        resp = client.post(
            "/api/cashout-records",
            json={"club_id": 2, "group_title": "RT / 1 / X", "amount": "10"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_create_missing_clubgto_404(self) -> None:
        self.db = _db_with_gto(None)
        self.app.dependency_overrides[get_db_dependency] = _override_db(self.db)
        client = TestClient(self.app)
        resp = client.post(
            "/api/cashout-records",
            json={"club_id": 7, "group_title": "GTO / 1 / X", "amount": "10"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_get_other_club_record_forbidden(self) -> None:
        other = {
            "id": 1,
            "club_id": 2,
            "group_title": "RT / 1 / X",
            "amount": Decimal("10"),
            "trigger": "manual",
            "tracks_money_sent": False,
            "do_not_send": False,
            "sent": Decimal("0"),
            "remaining": Decimal("10"),
            "status": "active",
            "payments": [],
            "sends": [],
        }
        with patch(
            "api.routes.cashout_records.get_staff_cashout_record",
            return_value=other,
        ):
            client = TestClient(self.app)
            resp = client.get("/api/cashout-records/1")
        self.assertEqual(resp.status_code, 403)


class BonusGtoScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(os.environ, {"DASHBOARD_PASSWORD": "changeme"}, clear=False)
        self.env_patch.start()
        self.gto = _gto_club(7)
        self.db = _db_with_gto(self.gto)
        self.app = FastAPI()
        self.app.include_router(bonus_routes.router)
        self.app.dependency_overrides[get_current_admin] = lambda: ROLE_GTO
        self.app.dependency_overrides[get_db_dependency] = _override_db(self.db)

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.app.dependency_overrides.clear()

    def test_list_forces_clubgto(self) -> None:
        with patch(
            "api.routes.bonus.list_bonus_record_rows",
            return_value=[],
        ) as mock_list:
            client = TestClient(self.app)
            resp = client.get("/api/bonus/records")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_list.call_args.kwargs["club_id"], 7)

    def test_list_other_club_forbidden(self) -> None:
        client = TestClient(self.app)
        resp = client.get("/api/bonus/records?club_id=2")
        self.assertEqual(resp.status_code, 403)

    def test_create_other_club_forbidden(self) -> None:
        client = TestClient(self.app)
        resp = client.post(
            "/api/bonus/records",
            json={
                "club_id": 2,
                "group_title": "RT / 1 / X",
                "amount": "10",
                "bonus_type_id": None,
                "custom_description": "x",
            },
        )
        self.assertEqual(resp.status_code, 403)

    def test_create_missing_clubgto_404(self) -> None:
        self.db = _db_with_gto(None)
        self.app.dependency_overrides[get_db_dependency] = _override_db(self.db)
        client = TestClient(self.app)
        resp = client.post(
            "/api/bonus/records",
            json={
                "club_id": 7,
                "group_title": "GTO / 1 / X",
                "amount": "10",
                "bonus_type_id": None,
                "custom_description": "x",
            },
        )
        self.assertEqual(resp.status_code, 404)

    def test_patch_other_club_record_forbidden(self) -> None:
        record = MagicMock()
        record.club_id = 2
        self.db.query.return_value.get.return_value = record
        self.db.query.return_value.filter.return_value.first.return_value = self.gto
        client = TestClient(self.app)
        resp = client.patch("/api/bonus/records/9", json={"amount": "20"})
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
