"""GTO role: ClubGTO-scoped payments/clubs/cashouts/bonuses; pool-pay still denied."""

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
from api.schemas import ClubRead
from api.schemas_payments import OwnerPaymentSummary
from api.routes import all_payments as all_payments_routes
from api.routes import bonus as bonus_routes
from api.routes import cashout_records as cashout_routes
from api.routes import clubs as clubs_routes
from api.routes import manual_deposit_requests as mdr_routes
from api.routes import payments as payments_routes
from db.connection import get_db_dependency


def _minimal_club_read(club_id: int = 7, name: str = GTO_CLUB_NAME) -> ClubRead:
    return ClubRead(
        id=club_id,
        name=name,
        telegram_user_id=1,
        welcome_type=None,
        welcome_text=None,
        welcome_file_id=None,
        welcome_caption=None,
        member_join_preamble_text=None,
        member_join_tos_file_id=None,
        member_join_tos_caption=None,
        list_type=None,
        list_text=None,
        list_file_id=None,
        list_caption=None,
        allow_multi_cashout=False,
        allow_admin_commands=False,
        deposit_simple_mode=False,
        deposit_simple_type=None,
        deposit_simple_text=None,
        deposit_simple_file_id=None,
        deposit_simple_caption=None,
        cashout_simple_mode=False,
        cashout_simple_type=None,
        cashout_simple_text=None,
        cashout_simple_file_id=None,
        cashout_simple_caption=None,
        cashout_cooldown_enabled=False,
        cashout_cooldown_hours=24,
        cashout_hours_enabled=False,
        cashout_hours_start=None,
        cashout_hours_end=None,
        referral_enabled=False,
        first_deposit_bonus_enabled=False,
        first_deposit_bonus_pct=0,
        is_active=True,
        created_at=None,
    )


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

    def test_gto_providers_allowed(self) -> None:
        self.app.dependency_overrides[get_current_admin] = lambda: ROLE_GTO
        client = TestClient(self.app)
        resp = client.get("/api/payments/providers")
        self.assertEqual(resp.status_code, 200)

    def test_am_allowed(self) -> None:
        self.app.dependency_overrides[get_current_admin] = lambda: ROLE_ACCOUNT_MANAGER
        client = TestClient(self.app)
        resp = client.get("/api/payments/providers")
        self.assertEqual(resp.status_code, 200)


class AllPaymentsGtoScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(os.environ, {"DASHBOARD_PASSWORD": "changeme"}, clear=False)
        self.env_patch.start()
        self.gto = _gto_club(7)
        self.db = _db_with_gto(self.gto)
        self.app = FastAPI()
        self.app.include_router(all_payments_routes.router)
        self.app.dependency_overrides[get_current_admin] = lambda: ROLE_GTO
        self.app.dependency_overrides[get_db_dependency] = _override_db(self.db)

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.app.dependency_overrides.clear()

    def test_list_forces_clubgto(self) -> None:
        summary = OwnerPaymentSummary(
            total_count=0,
            total_amount_cents=0,
            total_amount_usd=Decimal("0"),
        )
        with patch(
            "api.routes.all_payments.fetch_unified_page",
            return_value=([], 0, summary),
        ) as mock_fetch, patch(
            "api.routes.all_payments._get_club_or_404",
            return_value=self.gto,
        ):
            client = TestClient(self.app)
            resp = client.get("/api/payments/all/payments")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_fetch.call_args.kwargs["filters"].club_id, 7)

    def test_list_other_club_forbidden(self) -> None:
        client = TestClient(self.app)
        resp = client.get("/api/payments/all/payments?club_id=2")
        self.assertEqual(resp.status_code, 403)


class ClubsGtoScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(os.environ, {"DASHBOARD_PASSWORD": "changeme"}, clear=False)
        self.env_patch.start()
        self.gto = _gto_club(7)
        self.other = MagicMock()
        self.other.id = 2
        self.other.name = "Round Table"
        self.db = MagicMock()
        q = MagicMock()
        q.filter.return_value.first.return_value = self.gto
        q.order_by.return_value.all.return_value = [self.other, self.gto]
        q.get.side_effect = lambda cid: self.gto if cid == 7 else self.other
        self.db.query.return_value = q
        self.app = FastAPI()
        self.app.include_router(clubs_routes.router)
        self.app.dependency_overrides[get_current_admin] = lambda: ROLE_GTO
        self.app.dependency_overrides[get_db_dependency] = _override_db(self.db)

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.app.dependency_overrides.clear()

    def test_list_only_clubgto(self) -> None:
        with patch("api.routes.clubs._club_to_read") as to_read:
            to_read.side_effect = lambda c: _minimal_club_read(c.id, c.name)
            client = TestClient(self.app)
            client.get("/api/clubs")
        self.assertEqual(to_read.call_count, 1)
        self.assertEqual(to_read.call_args[0][0].id, 7)

    def test_get_other_club_forbidden(self) -> None:
        client = TestClient(self.app)
        resp = client.get("/api/clubs/2")
        self.assertEqual(resp.status_code, 403)

    def test_create_forbidden(self) -> None:
        client = TestClient(self.app)
        resp = client.post(
            "/api/clubs",
            json={"name": "X", "telegram_user_id": 1},
        )
        self.assertEqual(resp.status_code, 403)


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
