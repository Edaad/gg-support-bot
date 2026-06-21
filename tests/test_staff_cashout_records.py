"""Tests for staff cashout records service, Zapier payload, and API routes."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import get_current_admin
from api.routes.cashout_records import router
from cashier.services.zapier import (
    build_zapier_payload_from_cashout_record,
    build_zapier_name,
)
from db.connection import get_db_dependency


def _sample_record() -> dict:
    return {
        "id": 1,
        "cashier_job_id": 10,
        "club_id": 2,
        "chat_id": -100123,
        "group_title": "RT / 2427-3267 / Samin",
        "gg_player_id": "2427-3267",
        "amount": Decimal("500"),
        "recorded_by_telegram_user_id": 999,
        "trigger": "group_cash",
        "created_at": None,
        "updated_at": None,
        "payments": [
            {
                "id": 5,
                "cashout_record_id": 1,
                "payment_method_id": None,
                "payment_sub_option_id": None,
                "method_display_name": "Venmo",
                "payout_details": "@player",
                "amount": Decimal("500"),
                "sort_order": 0,
            }
        ],
    }


def _make_api_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    def override_admin():
        return "admin"

    def override_db():
        yield MagicMock()

    app.dependency_overrides[get_current_admin] = override_admin
    app.dependency_overrides[get_db_dependency] = override_db
    return app


class ZapierPayloadTestCase(unittest.TestCase):
    def test_build_zapier_name_parses_title(self) -> None:
        name = build_zapier_name("RT / 2427-3267 / Samin")
        self.assertEqual(name, "RT / 2427-3267 / Samin")

    def test_build_payload_from_record_uses_primary_payment(self) -> None:
        record = _sample_record()
        payload, err = build_zapier_payload_from_cashout_record(
            record, record["payments"]
        )
        self.assertIsNone(err)
        assert payload is not None
        self.assertEqual(payload["name"], "RT / 2427-3267 / Samin")
        self.assertEqual(payload["opening_balance"], 500.0)
        self.assertEqual(payload["other"], "@player")

    def test_build_payload_fails_without_payment(self) -> None:
        record = _sample_record()
        payload, err = build_zapier_payload_from_cashout_record(record, [])
        self.assertIsNone(payload)
        self.assertIn("no payment method", err or "")


class StaffCashoutRecordServiceTestCase(unittest.TestCase):
    def test_create_idempotent_when_record_exists(self) -> None:
        existing = MagicMock()
        existing.id = 42
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = existing
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch("bot.services.staff_cashout_records.get_db", return_value=cm):
            from bot.services.staff_cashout_records import (
                create_staff_cashout_record_from_job,
            )

            job = {
                "id": 10,
                "club_id": 2,
                "chat_id": -100,
                "group_title": "RT / 1-2 / X",
                "amount": Decimal("100"),
                "initiated_by": 1,
                "trigger": "group_cash",
            }
            rid = create_staff_cashout_record_from_job(job)
            self.assertEqual(rid, 42)
            session.add.assert_not_called()

    def test_delete_last_payment_raises(self) -> None:
        record = MagicMock()
        record.payments = [MagicMock(id=1)]
        session = MagicMock()
        session.get.side_effect = lambda _cls, _id: record if _id == 1 else MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch("bot.services.staff_cashout_records.get_db", return_value=cm):
            from bot.services.staff_cashout_records import delete_staff_cashout_payment

            with self.assertRaises(ValueError):
                delete_staff_cashout_payment(1, 1)


class CashoutRecordsApiTestCase(unittest.TestCase):
    def test_list_returns_records(self) -> None:
        with patch(
            "api.routes.cashout_records.list_staff_cashout_records",
            return_value=[_sample_record()],
        ), patch(
            "api.routes.cashout_records._club_name_map",
            return_value={2: "Round Table"},
        ):
            client = TestClient(_make_api_app())
            resp = client.get("/api/cashout-records")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(len(body), 1)
            self.assertEqual(body[0]["group_title"], "RT / 2427-3267 / Samin")
            self.assertEqual(body[0]["club_name"], "Round Table")

    def test_patch_triggers_zapier_sync(self) -> None:
        updated = _sample_record()
        updated["group_title"] = "RT / 2427-3267 / Sam"
        with patch(
            "api.routes.cashout_records.update_staff_cashout_record",
            return_value=updated,
        ), patch(
            "api.routes.cashout_records._club_name_map",
            return_value={2: "Round Table"},
        ), patch(
            "api.routes.cashout_records.fire_zapier_webhook_for_record",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_zap:
            client = TestClient(_make_api_app())
            resp = client.patch(
                "/api/cashout-records/1",
                json={"group_title": "RT / 2427-3267 / Sam"},
            )
            self.assertEqual(resp.status_code, 200)
            mock_zap.assert_awaited_once_with(1)

    def test_patch_returns_502_on_zapier_failure(self) -> None:
        updated = _sample_record()
        with patch(
            "api.routes.cashout_records.update_staff_cashout_record",
            return_value=updated,
        ), patch(
            "api.routes.cashout_records.fire_zapier_webhook_for_record",
            new=AsyncMock(return_value=(False, "Zapier webhook failed")),
        ):
            client = TestClient(_make_api_app())
            resp = client.patch(
                "/api/cashout-records/1",
                json={"amount": "600"},
            )
            self.assertEqual(resp.status_code, 502)

    def test_delete_last_payment_returns_400(self) -> None:
        with patch(
            "api.routes.cashout_records.delete_staff_cashout_payment",
            side_effect=ValueError("Cannot delete the last payment line"),
        ):
            client = TestClient(_make_api_app())
            resp = client.delete("/api/cashout-records/1/payments/5")
            self.assertEqual(resp.status_code, 400)


class CompleteCashoutHookTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_complete_creates_staff_record_after_zapier(self) -> None:
        job = {
            "id": 7,
            "club_id": 2,
            "chat_id": -100,
            "group_title": "RT / 1-2 / X",
            "amount": Decimal("50"),
            "status": "in_progress",
            "initiated_by": 1,
            "trigger": "group_cash",
            "method_display_name": "Venmo",
            "payout_details": "@x",
        }
        with patch(
            "cashier.services.complete.get_job",
            return_value=job,
        ), patch(
            "cashier.services.complete.fire_zapier_webhook",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "cashier.services.complete.create_staff_cashout_record_from_job",
            return_value=99,
        ) as mock_create, patch(
            "cashier.services.complete.schedule_cash_flow_from_club",
        ), patch(
            "cashier.services.complete.record_activity_for_chat",
        ), patch(
            "cashier.services.complete.invalidate_pending_one_time_bypasses",
        ), patch(
            "cashier.services.complete.complete_job",
            return_value=job,
        ):
            from cashier.services.complete import complete_cashout_job

            ok, err = await complete_cashout_job(7)
            self.assertTrue(ok)
            self.assertIsNone(err)
            mock_create.assert_called_once_with(job)


if __name__ == "__main__":
    unittest.main()
