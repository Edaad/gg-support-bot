"""Tests for staff cashout records service, Zapier payload, and API routes."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import ROLE_ACCOUNT_MANAGER, get_current_admin
from api.routes.cashout_records import router
from bot.services.staff_cashout_records import (
    CashoutRecordNotActive,
    _matches_search,
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
        "do_not_send": False,
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


class SearchMatchTestCase(unittest.TestCase):
    def test_matches_name_case_insensitive(self) -> None:
        row = {"group_title": "GTO / 3580-8055 / Sijan", "gg_player_id": "3580-8055"}
        self.assertTrue(_matches_search(row, "sijan"))
        self.assertFalse(
            _matches_search(
                {"group_title": "GTO / 2690-5329 / @Mrhulkx", "gg_player_id": "2690-5329"},
                "sijan",
            )
        )


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

    def test_build_zapier_name_cc_at_keeps_cc_first(self) -> None:
        name = build_zapier_name("CC AT / 8879-5560 / V")
        self.assertEqual(name, "CC AT / 8879-5560 / V")

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

    def test_money_sends_ledger_admin_ok(self) -> None:
        row = {
            "id": 9,
            "cashout_record_id": 1,
            "sender_name": "Rtsupport",
            "amount": Decimal("385"),
            "payment_method_id": None,
            "payment_sub_option_id": None,
            "method_display_name": "Venmo",
            "created_at": None,
            "club_id": 2,
            "club_name": "Round Table",
            "group_title": "RT AT / 4283-2447 / Raff",
            "gg_player_id": "4283-2447",
        }
        with patch(
            "api.routes.cashout_records.list_staff_cashout_money_sends",
            return_value=[row],
        ) as mock_list:
            client = TestClient(_make_api_app())
            resp = client.get(
                "/api/cashout-records/sends?from=2026-07-22&to=2026-08-21&club_id=2&q=Raff&method=Venmo"
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["sender_name"], "Rtsupport")
        self.assertEqual(body[0]["group_title"], "RT AT / 4283-2447 / Raff")
        kwargs = mock_list.call_args.kwargs
        self.assertEqual(kwargs["club_id"], 2)
        self.assertEqual(kwargs["method_display_name"], "Venmo")
        self.assertEqual(kwargs["q"], "Raff")
        self.assertIsNotNone(kwargs["from_dt"])
        self.assertIsNotNone(kwargs["to_dt"])

    def test_money_sends_ledger_am_forbidden(self) -> None:
        app = _make_api_app()
        app.dependency_overrides[get_current_admin] = lambda: ROLE_ACCOUNT_MANAGER
        client = TestClient(app)
        resp = client.get("/api/cashout-records/sends?from=2026-07-22&to=2026-08-21")
        self.assertEqual(resp.status_code, 403)

    def test_money_sends_export_am_forbidden(self) -> None:
        app = _make_api_app()
        app.dependency_overrides[get_current_admin] = lambda: ROLE_ACCOUNT_MANAGER
        client = TestClient(app)
        resp = client.get("/api/cashout-records/sends/export?from=2026-07-22&to=2026-08-21")
        self.assertEqual(resp.status_code, 403)

    def test_money_sends_methods_admin_ok(self) -> None:
        with patch(
            "api.routes.cashout_records.list_money_send_method_names",
            return_value=["Venmo", "Zelle"],
        ):
            client = TestClient(_make_api_app())
            resp = client.get("/api/cashout-records/sends/methods?from=2026-07-22&to=2026-08-21")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), ["Venmo", "Zelle"])

    def test_money_sends_export_admin_ok(self) -> None:
        with patch(
            "api.routes.cashout_records.build_cashout_money_sends_csv",
            return_value=b"amount,sender_name\n",
        ):
            client = TestClient(_make_api_app())
            resp = client.get("/api/cashout-records/sends/export?from=2026-07-22&to=2026-08-21")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.headers.get("content-type", ""))

    def test_list_do_not_send_admin_ok(self) -> None:
        parked = _sample_record()
        parked["do_not_send"] = True
        with patch(
            "api.routes.cashout_records.list_staff_cashout_records",
            return_value=[parked],
        ) as mock_list, patch(
            "api.routes.cashout_records._club_name_map",
            return_value={2: "Round Table"},
        ):
            client = TestClient(_make_api_app())
            resp = client.get("/api/cashout-records?status=do_not_send")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()[0]["do_not_send"])
        self.assertEqual(mock_list.call_args.kwargs["status"], "do_not_send")

    def test_list_do_not_send_am_forbidden(self) -> None:
        app = _make_api_app()
        app.dependency_overrides[get_current_admin] = lambda: ROLE_ACCOUNT_MANAGER
        client = TestClient(app)
        resp = client.get("/api/cashout-records?status=do_not_send")
        self.assertEqual(resp.status_code, 403)

    def test_patch_do_not_send_admin_ok(self) -> None:
        updated = _sample_record()
        updated["do_not_send"] = True
        with patch(
            "api.routes.cashout_records.update_staff_cashout_record",
            return_value=updated,
        ) as mock_update, patch(
            "api.routes.cashout_records._club_name_map",
            return_value={2: "Round Table"},
        ):
            client = TestClient(_make_api_app())
            resp = client.patch("/api/cashout-records/1", json={"do_not_send": True})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["do_not_send"])
        self.assertEqual(mock_update.call_args.kwargs["do_not_send"], True)

    def test_patch_do_not_send_am_forbidden(self) -> None:
        app = _make_api_app()
        app.dependency_overrides[get_current_admin] = lambda: ROLE_ACCOUNT_MANAGER
        client = TestClient(app)
        resp = client.patch("/api/cashout-records/1", json={"do_not_send": True})
        self.assertEqual(resp.status_code, 403)

    def test_patch_title_am_allowed(self) -> None:
        updated = _sample_record()
        updated["group_title"] = "RT / 1 / X"
        app = _make_api_app()
        app.dependency_overrides[get_current_admin] = lambda: ROLE_ACCOUNT_MANAGER
        with patch(
            "api.routes.cashout_records.update_staff_cashout_record",
            return_value=updated,
        ), patch(
            "api.routes.cashout_records._club_name_map",
            return_value={2: "Round Table"},
        ):
            client = TestClient(app)
            resp = client.patch(
                "/api/cashout-records/1",
                json={"group_title": "RT / 1 / X"},
            )
        self.assertEqual(resp.status_code, 200)


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
            "cashier.services.complete.apply_low_deposit_cashout_hold",
            return_value=None,
        ) as mock_hold, patch(
            "cashier.services.complete.notify_slack_escalation",
            new=AsyncMock(return_value=True),
        ) as mock_slack, patch(
            "cashier.services.complete.notify_slack_head_admin_escalation",
            new=AsyncMock(return_value=True),
        ) as mock_head_admin, patch(
            "cashier.services.complete.dm_staff",
            new=AsyncMock(return_value=True),
        ) as mock_dm, patch(
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
            mock_hold.assert_called_once_with(99)
            mock_slack.assert_not_called()
            mock_head_admin.assert_not_called()
            mock_dm.assert_not_called()

    async def test_complete_notifies_slack_when_low_deposit_hold(self) -> None:
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
        hold = {
            "record_id": 99,
            "club_id": 2,
            "chat_id": -100,
            "group_title": "RT / 1-2 / X",
            "gg_player_id": "1-2",
            "amount": Decimal("50"),
            "deposit_count": 0,
            "reason": "no_deposits",
        }
        club = MagicMock()
        club.name = "Round Table"
        with patch(
            "cashier.services.complete.get_job",
            return_value=job,
        ), patch(
            "cashier.services.complete.fire_zapier_webhook",
            new=AsyncMock(return_value=(True, None)),
        ) as mock_zapier, patch(
            "cashier.services.complete.create_staff_cashout_record_from_job",
            return_value=99,
        ), patch(
            "cashier.services.complete.apply_low_deposit_cashout_hold",
            return_value=hold,
        ), patch(
            "cashier.services.complete.notify_slack_escalation",
            new=AsyncMock(return_value=True),
        ) as mock_slack, patch(
            "cashier.services.complete.notify_slack_head_admin_escalation",
            new=AsyncMock(return_value=True),
        ) as mock_head_admin, patch(
            "cashier.services.complete.dm_staff",
            new=AsyncMock(return_value=True),
        ) as mock_dm, patch(
            "cashier.services.complete.get_club_by_id",
            return_value=club,
        ), patch(
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
            mock_zapier.assert_awaited_once()
            mock_slack.assert_awaited_once()
            text = mock_slack.await_args.args[0]
            self.assertTrue(
                text.startswith(
                    "CASHOUT ON HOLD, DO NOT SEND UNTIL HEAD ADMIN CLEARS\n"
                )
            )
            self.assertIn("0 deposits", text)
            self.assertIn("Round Table", text)
            self.assertIn("*Group*: `RT / 1-2 / X`", text)
            self.assertIn("*Club*: Round Table", text)
            self.assertNotIn("Chat id", text)
            self.assertEqual(
                mock_slack.await_args.kwargs.get("source"),
                "low_deposit_cashout",
            )
            mock_head_admin.assert_awaited_once_with(
                text, source="low_deposit_cashout"
            )
            mock_dm.assert_awaited_once()
            self.assertEqual(mock_dm.await_args.args[0], 1)
            tg = mock_dm.await_args.args[1]
            self.assertIn("<b>Group</b>: <code>RT / 1-2 / X</code>", tg)
            self.assertIn("<b>Club</b>: Round Table", tg)
            self.assertEqual(mock_dm.await_args.kwargs.get("parse_mode"), "HTML")


class CountDepositsForChatTestCase(unittest.TestCase):
    @patch("bot.services.club.get_db")
    def test_counts_non_cancelled_deposits(self, mock_get_db: MagicMock) -> None:
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        mock_get_db.return_value.__exit__.return_value = False
        session.query.return_value.filter_by.return_value.filter.return_value.count.return_value = (
            3
        )

        from bot.services.club import count_deposits_for_chat
        from db.models import PlayerActivity

        self.assertEqual(count_deposits_for_chat(-100), 3)
        session.query.assert_called_once_with(PlayerActivity)
        session.query.return_value.filter_by.assert_called_once_with(
            chat_id=-100,
            activity_type="deposit",
        )


class LowDepositCashoutHoldTestCase(unittest.TestCase):
    def _session_cm(self, session: MagicMock) -> MagicMock:
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False
        return cm

    def test_skip_when_no_chat_id(self) -> None:
        record = MagicMock()
        record.chat_id = None
        record.do_not_send = False
        session = MagicMock()
        session.get.return_value = record

        with patch(
            "bot.services.staff_cashout_records.get_db",
            return_value=self._session_cm(session),
        ):
            from bot.services.staff_cashout_records import apply_low_deposit_cashout_hold

            self.assertIsNone(apply_low_deposit_cashout_hold(1))
            self.assertFalse(record.do_not_send)

    def test_skip_when_already_do_not_send(self) -> None:
        record = MagicMock()
        record.chat_id = -100
        record.do_not_send = True
        session = MagicMock()
        session.get.return_value = record

        with patch(
            "bot.services.staff_cashout_records.get_db",
            return_value=self._session_cm(session),
        ), patch(
            "bot.services.staff_cashout_records.count_deposits_for_chat",
        ) as mock_count:
            from bot.services.staff_cashout_records import apply_low_deposit_cashout_hold

            self.assertIsNone(apply_low_deposit_cashout_hold(1))
            mock_count.assert_not_called()

    def test_no_hold_when_two_or_more_deposits(self) -> None:
        record = MagicMock()
        record.id = 5
        record.chat_id = -100
        record.do_not_send = False
        session = MagicMock()
        session.get.return_value = record

        with patch(
            "bot.services.staff_cashout_records.get_db",
            return_value=self._session_cm(session),
        ), patch(
            "bot.services.staff_cashout_records.count_deposits_for_chat",
            return_value=2,
        ):
            from bot.services.staff_cashout_records import apply_low_deposit_cashout_hold

            self.assertIsNone(apply_low_deposit_cashout_hold(5))
            self.assertFalse(record.do_not_send)

    def test_parks_on_zero_deposits(self) -> None:
        record = MagicMock()
        record.id = 5
        record.club_id = 2
        record.chat_id = -100
        record.group_title = "RT / 1 / X"
        record.gg_player_id = "1"
        record.amount = Decimal("75")
        record.do_not_send = False
        session = MagicMock()
        session.get.return_value = record

        with patch(
            "bot.services.staff_cashout_records.get_db",
            return_value=self._session_cm(session),
        ), patch(
            "bot.services.staff_cashout_records.count_deposits_for_chat",
            return_value=0,
        ):
            from bot.services.staff_cashout_records import apply_low_deposit_cashout_hold

            hold = apply_low_deposit_cashout_hold(5)
        self.assertTrue(record.do_not_send)
        self.assertEqual(hold["reason"], "no_deposits")
        self.assertEqual(hold["deposit_count"], 0)
        self.assertEqual(hold["record_id"], 5)

    def test_parks_on_single_deposit(self) -> None:
        record = MagicMock()
        record.id = 6
        record.club_id = 2
        record.chat_id = -100
        record.group_title = "RT / 1 / X"
        record.gg_player_id = "1"
        record.amount = Decimal("75")
        record.do_not_send = False
        session = MagicMock()
        session.get.return_value = record

        with patch(
            "bot.services.staff_cashout_records.get_db",
            return_value=self._session_cm(session),
        ), patch(
            "bot.services.staff_cashout_records.count_deposits_for_chat",
            return_value=1,
        ):
            from bot.services.staff_cashout_records import apply_low_deposit_cashout_hold

            hold = apply_low_deposit_cashout_hold(6)
        self.assertTrue(record.do_not_send)
        self.assertEqual(hold["reason"], "single_deposit")
        self.assertEqual(hold["deposit_count"], 1)

    def test_parks_on_count_exception(self) -> None:
        record = MagicMock()
        record.id = 7
        record.club_id = 2
        record.chat_id = -100
        record.group_title = "RT / 1 / X"
        record.gg_player_id = "1"
        record.amount = Decimal("75")
        record.do_not_send = False
        session = MagicMock()
        session.get.return_value = record

        with patch(
            "bot.services.staff_cashout_records.get_db",
            return_value=self._session_cm(session),
        ), patch(
            "bot.services.staff_cashout_records.count_deposits_for_chat",
            side_effect=RuntimeError("db down"),
        ):
            from bot.services.staff_cashout_records import apply_low_deposit_cashout_hold

            hold = apply_low_deposit_cashout_hold(7)
        self.assertTrue(record.do_not_send)
        self.assertEqual(hold["reason"], "count_failed")
        self.assertIsNone(hold["deposit_count"])


if __name__ == "__main__":
    unittest.main()
