"""API tests for manual deposit request dashboard create/edit."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import get_current_admin
from api.routes.manual_deposit_requests import router
from db.connection import get_db_dependency
from db.models import ManualDepositRequest


def _make_app(mock_db: MagicMock | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    db = mock_db or MagicMock()

    def override_db():
        yield db

    app.dependency_overrides[get_db_dependency] = override_db
    app.dependency_overrides[get_current_admin] = lambda: "admin"
    return app


def _sample_row(**overrides) -> ManualDepositRequest:
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    data = dict(
        id=1,
        club_id=1,
        method_id=10,
        method_name="Zelle",
        method_slug="zelle-union",
        variant_name="zelle-union",
        group_title="RT / 1111-1111 / Alice",
        amount=Decimal("100"),
        telegram_chat_id=-1001,
        trade_record_checked=False,
        source="dashboard",
        created_at=now,
        club=SimpleNamespace(id=1, name="Round Table"),
    )
    data.update(overrides)
    row = ManualDepositRequest(**{k: v for k, v in data.items() if k != "club"})
    row.club = data["club"]
    return row


class ManualDepositRequestsApiTests(unittest.TestCase):
    def test_list_deposit_groups(self) -> None:
        from db.models import ClubPaymentMethod, Group

        method = SimpleNamespace(
            id=10,
            name="Zelle",
            tracks_manual_requests=True,
        )
        group = SimpleNamespace(
            chat_id=-1001,
            club_id=1,
            name="RT / 1111-1111 / Alice",
        )
        db = MagicMock()
        chain = MagicMock()
        chain.join.return_value = chain
        chain.filter.return_value = chain
        chain.order_by.return_value = chain
        chain.limit.return_value = chain
        chain.all.return_value = [(group, "Round Table")]

        def _query(*args):
            if args and args[0] is ClubPaymentMethod:
                m = MagicMock()
                m.get.return_value = method
                return m
            return chain

        db.query.side_effect = _query
        with patch(
            "api.routes.manual_deposit_requests.method_club_ids",
            return_value={1},
        ):
            client = TestClient(_make_app(db))
            res = client.get("/api/v2/methods/10/deposit-groups?q=Alice")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["chat_id"], -1001)

    def test_post_create_notifies(self) -> None:
        method = SimpleNamespace(
            id=10,
            name="Zelle",
            tracks_manual_requests=True,
        )
        row = _sample_row()
        db = MagicMock()
        db.query.return_value.get.return_value = method
        db.query.return_value.options.return_value.filter.return_value.first.return_value = (
            row
        )
        with patch(
            "api.routes.manual_deposit_requests.union_deposit_slack_variant",
            return_value="first",
        ), patch(
            "api.routes.manual_deposit_requests.create_dashboard_manual_deposit_request",
            return_value=row,
        ), patch(
            "bot.services.escalation_notification.notify_union_deposit_request_slack",
            new=AsyncMock(return_value=True),
        ) as notify:
            client = TestClient(_make_app(db))
            res = client.post(
                "/api/v2/methods/10/manual-deposit-requests",
                json={
                    "amount": "100",
                    "telegram_chat_id": -1001,
                    "trade_record_checked": False,
                },
            )
        self.assertEqual(res.status_code, 201)
        notify.assert_awaited_once()

    def test_post_create_validation_error(self) -> None:
        from bot.services.manual_deposit_requests import ManualDepositValidationError

        method = SimpleNamespace(
            id=10,
            name="Zelle",
            tracks_manual_requests=True,
        )
        db = MagicMock()
        db.query.return_value.get.return_value = method
        with patch(
            "api.routes.manual_deposit_requests.union_deposit_slack_variant",
            return_value="first",
        ), patch(
            "api.routes.manual_deposit_requests.create_dashboard_manual_deposit_request",
            side_effect=ManualDepositValidationError("Amount must be positive."),
        ):
            client = TestClient(_make_app(db))
            res = client.post(
                "/api/v2/methods/10/manual-deposit-requests",
                json={
                    "amount": "100",
                    "telegram_chat_id": -1001,
                },
            )
        self.assertEqual(res.status_code, 400)

    def test_patch_update_no_notify(self) -> None:
        row = _sample_row(trade_record_checked=True)
        db = MagicMock()
        db.query.return_value.options.return_value.filter.return_value.first.return_value = (
            row
        )
        with patch(
            "api.routes.manual_deposit_requests.update_dashboard_manual_deposit_request",
            return_value=row,
        ), patch(
            "bot.services.escalation_notification.notify_union_deposit_request_slack",
            new=AsyncMock(return_value=True),
        ) as notify:
            client = TestClient(_make_app(db))
            res = client.patch(
                "/api/manual-deposit-requests/1",
                json={"amount": "150", "trade_record_checked": True},
            )
        self.assertEqual(res.status_code, 200)
        notify.assert_not_called()
        self.assertEqual(Decimal(res.json()["amount"]), Decimal("100"))


if __name__ == "__main__":
    unittest.main()
