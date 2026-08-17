"""Tests for staff cashout records service, Zapier payload, and API routes."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import get_current_admin
from api.routes.cashout_records import router
from bot.services.staff_cashout_records import (
    CashoutRecordNotActive,
    compute_ledger,
)
from cashier.services.zapier import (
    build_zapier_name,
    build_zapier_payload_from_cashout_record,
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
        "tracks_money_sent": True,
        "sent": Decimal("0"),
        "remaining": Decimal("500"),
        "status": "active",
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
                "amount": None,
                "sort_order": 0,
                "created_at": None,
            }
        ],
        "sends": [],
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


class LedgerStatusTestCase(unittest.TestCase):
    def test_legacy_always_cleared(self) -> None:
        ledger = compute_ledger(False, Decimal("300"), [{"amount": Decimal("50")}])
        self.assertEqual(ledger["status"], "cleared")
        self.assertEqual(ledger["sent"], Decimal("0"))
        self.assertEqual(ledger["remaining"], Decimal("0"))

    def test_new_zero_sent_is_active(self) -> None:
        ledger = compute_ledger(True, Decimal("300"), [])
        self.assertEqual(ledger["status"], "active")
        self.assertEqual(ledger["sent"], Decimal("0"))
        self.assertEqual(ledger["remaining"], Decimal("300"))

    def test_equal_is_cleared(self) -> None:
        ledger = compute_ledger(True, Decimal("300"), [{"amount": Decimal("300")}])
        self.assertEqual(ledger["status"], "cleared")
        self.assertEqual(ledger["remaining"], Decimal("0"))

    def test_oversent(self) -> None:
        ledger = compute_ledger(
            True,
            Decimal("300"),
            [{"amount": Decimal("200")}, {"amount": Decimal("150")}],
        )
        self.assertEqual(ledger["status"], "oversent")
        self.assertEqual(ledger["sent"], Decimal("350"))
        self.assertEqual(ledger["remaining"], Decimal("-50"))


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

    def test_create_sets_tracks_money_sent(self) -> None:
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None

        def flush() -> None:
            added = session.add.call_args_list[0][0][0]
            added.id = 7

        session.flush.side_effect = flush
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch("bot.services.staff_cashout_records.get_db", return_value=cm):
            from bot.services.staff_cashout_records import (
                create_staff_cashout_record_from_job,
            )
            from db.models import StaffCashoutRecord

            job = {
                "id": 10,
                "club_id": 2,
                "chat_id": -100,
                "group_title": "RT / 1-2 / X",
                "amount": Decimal("100"),
                "initiated_by": 1,
                "trigger": "group_cash",
                "method_display_name": "Zelle",
                "payout_details": "408",
            }
            rid = create_staff_cashout_record_from_job(job)
            self.assertEqual(rid, 7)
            record = session.add.call_args_list[0][0][0]
            self.assertIsInstance(record, StaffCashoutRecord)
            self.assertTrue(record.tracks_money_sent)

    def test_update_amount_blocked_when_not_active(self) -> None:
        from db.models import StaffCashoutRecord

        record = MagicMock(spec=StaffCashoutRecord)
        record.id = 1
        record.cashier_job_id = 1
        record.club_id = 2
        record.chat_id = -1
        record.group_title = "RT / 1 / X"
        record.gg_player_id = "1"
        record.amount = Decimal("100")
        record.recorded_by_telegram_user_id = 1
        record.trigger = "group_cash"
        record.tracks_money_sent = True
        record.created_at = None
        record.updated_at = None
        record.payments = []
        record.money_sends = [MagicMock(
            id=1,
            cashout_record_id=1,
            sender_name="A",
            amount=Decimal("100"),
            payment_method_id=None,
            payment_sub_option_id=None,
            method_display_name="Zelle",
            created_at=None,
        )]
        session = MagicMock()
        session.get.return_value = record
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch("bot.services.staff_cashout_records.get_db", return_value=cm):
            from bot.services.staff_cashout_records import update_staff_cashout_record

            with self.assertRaises(CashoutRecordNotActive):
                update_staff_cashout_record(1, amount=Decimal("200"))

    def test_custom_destination_skips_method_lookup(self) -> None:
        from db.models import StaffCashoutRecord

        record = MagicMock(spec=StaffCashoutRecord)
        record.id = 1
        record.cashier_job_id = 1
        record.club_id = 2
        record.chat_id = -1
        record.group_title = "RT / 1 / X"
        record.gg_player_id = "1"
        record.amount = Decimal("100")
        record.recorded_by_telegram_user_id = 1
        record.trigger = "group_cash"
        record.tracks_money_sent = True
        record.created_at = None
        record.updated_at = None
        record.payments = []
        record.money_sends = []
        session = MagicMock()
        session.get.return_value = record
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch("bot.services.staff_cashout_records.get_db", return_value=cm), patch(
            "bot.services.staff_cashout_records.get_method_by_id"
        ) as mock_method:
            from bot.services.staff_cashout_records import add_staff_cashout_payment

            add_staff_cashout_payment(
                1,
                {
                    "payment_method_id": None,
                    "method_display_name": "Revolut",
                    "payout_details": "",
                },
            )
            mock_method.assert_not_called()
            added = session.add.call_args[0][0]
            self.assertEqual(added.method_display_name, "Revolut")
            self.assertIsNone(added.payment_method_id)


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
            resp = client.get("/api/cashout-records?status=active")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(len(body), 1)
            self.assertEqual(body[0]["group_title"], "RT / 2427-3267 / Samin")
            self.assertEqual(body[0]["club_name"], "Round Table")
            self.assertEqual(body[0]["status"], "active")

    def test_list_passes_club_and_search(self) -> None:
        with patch(
            "api.routes.cashout_records.list_staff_cashout_records",
            return_value=[],
        ) as mock_list, patch(
            "api.routes.cashout_records._club_name_map",
            return_value={},
        ):
            client = TestClient(_make_api_app())
            resp = client.get("/api/cashout-records?status=active&club_id=2&q=Samin")
        self.assertEqual(resp.status_code, 200)
        mock_list.assert_called_once()
        kwargs = mock_list.call_args.kwargs
        self.assertEqual(kwargs["club_id"], 2)
        self.assertEqual(kwargs["status"], "active")
        self.assertEqual(kwargs["q"], "Samin")

    def test_create_manual_cashout(self) -> None:
        created = _sample_record()
        created["cashier_job_id"] = None
        created["chat_id"] = None
        created["recorded_by_telegram_user_id"] = None
        created["trigger"] = "dashboard"
        with patch(
            "api.routes.cashout_records.create_staff_cashout_record_manual",
            return_value=created,
        ), patch(
            "api.routes.cashout_records._club_name_map",
            return_value={2: "Round Table"},
        ):
            client = TestClient(_make_api_app())
            resp = client.post(
                "/api/cashout-records",
                json={"club_id": 2, "group_title": "RT / 2427-3267 / Samin", "amount": "500"},
            )
            self.assertEqual(resp.status_code, 201)
            self.assertEqual(resp.json()["trigger"], "dashboard")
            self.assertIsNone(resp.json()["cashier_job_id"])

    def test_patch_does_not_call_zapier(self) -> None:
        updated = _sample_record()
        updated["group_title"] = "RT / 2427-3267 / Sam"
        with patch(
            "api.routes.cashout_records.update_staff_cashout_record",
            return_value=updated,
        ), patch(
            "api.routes.cashout_records._club_name_map",
            return_value={2: "Round Table"},
        ):
            client = TestClient(_make_api_app())
            resp = client.patch(
                "/api/cashout-records/1",
                json={"group_title": "RT / 2427-3267 / Sam"},
            )
            self.assertEqual(resp.status_code, 200)

    def test_patch_amount_conflict_when_not_active(self) -> None:
        with patch(
            "api.routes.cashout_records.update_staff_cashout_record",
            side_effect=CashoutRecordNotActive("Original amount can only be edited while active"),
        ):
            client = TestClient(_make_api_app())
            resp = client.patch("/api/cashout-records/1", json={"amount": "600"})
            self.assertEqual(resp.status_code, 409)

    def test_add_send_allows_oversend(self) -> None:
        updated = _sample_record()
        updated["sends"] = [
            {
                "id": 9,
                "sender_name": "Rtsupport",
                "amount": Decimal("600"),
                "payment_method_id": None,
                "payment_sub_option_id": None,
                "method_display_name": "Zelle",
                "created_at": None,
            }
        ]
        updated["sent"] = Decimal("600")
        updated["remaining"] = Decimal("-100")
        updated["status"] = "oversent"
        with patch(
            "api.routes.cashout_records.add_staff_cashout_send",
            return_value=updated,
        ), patch(
            "api.routes.cashout_records._club_name_map",
            return_value={2: "Round Table"},
        ):
            client = TestClient(_make_api_app())
            resp = client.post(
                "/api/cashout-records/1/sends",
                json={"sender_name": "Rtsupport", "amount": "600", "method_display_name": "Zelle"},
            )
            self.assertEqual(resp.status_code, 201)
            self.assertEqual(resp.json()["status"], "oversent")

    def test_delete_payment_ok(self) -> None:
        updated = _sample_record()
        updated["payments"] = []
        with patch(
            "api.routes.cashout_records.delete_staff_cashout_payment",
            return_value=updated,
        ), patch(
            "api.routes.cashout_records._club_name_map",
            return_value={2: "Round Table"},
        ):
            client = TestClient(_make_api_app())
            resp = client.delete("/api/cashout-records/1/payments/5")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["payments"], [])


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
