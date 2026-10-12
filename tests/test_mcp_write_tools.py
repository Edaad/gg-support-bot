"""Write tools: dry run without confirm, applied with confirm, validation errors."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from mcp.server.mcpserver.exceptions import ToolError

import mcp_server.db as mcp_db
from bot.services.venmo_payments import BindResult, BoundGroup
from db.models import (
    Club,
    ClubPaymentMethod,
    ClubPaymentTier,
    ClubPaymentTierVariant,
    Expense,
    ManualDepositRequest,
    StaffCashoutRecord,
    VenmoPayment,
)
from mcp_server.tools.cashouts import update_cashout_record
from mcp_server.tools.expenses import create_expense
from mcp_server.tools.ops import pause_variant, resume_variant
from mcp_server.tools.payments import bind_payment_to_group
from mcp_server.tools.pool_pay import update_manual_deposit_request
from tests.support.sqlite_db import get_db_for, make_sqlite_session_factory

ENV = {"MCP_API_TOKENS": "dev:" + "x" * 32, "MCP_ACTOR": "tester"}


class _WriteToolCase(unittest.TestCase):
    tables: list[str] = []

    def setUp(self) -> None:
        env = patch.dict(os.environ, ENV, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.factory = make_sqlite_session_factory(self.tables)
        mcp_db.session_factory = self.factory
        self.addCleanup(setattr, mcp_db, "session_factory", None)
        for module in (
            "bot.services.staff_cashout_records",
            "bot.services.bonus_records",
            "bot.services.manual_deposit_requests",
            "bot.services.club",
        ):
            patcher = patch(f"{module}.get_db", get_db_for(self.factory))
            patcher.start()
            self.addCleanup(patcher.stop)
        with self.factory() as session:
            session.add(Club(id=1, name="ClubGTO", telegram_user_id=1001))
            session.commit()


class UpdateCashoutRecordTests(_WriteToolCase):
    tables = [
        "clubs",
        "cashier_cashout_jobs",
        "staff_cashout_records",
        "staff_cashout_payments",
        "staff_cashout_money_sends",
    ]

    def setUp(self) -> None:
        super().setUp()
        with self.factory() as session:
            session.add(
                StaffCashoutRecord(
                    id=5,
                    club_id=1,
                    chat_id=-1001,
                    group_title="GTO / 8190-5287 / ThePirate343",
                    gg_player_id="8190-5287",
                    amount=Decimal("250.00"),
                    trigger="dashboard",
                    tracks_money_sent=True,
                )
            )
            session.commit()

    def _row(self) -> StaffCashoutRecord:
        with self.factory() as session:
            return session.get(StaffCashoutRecord, 5)

    def test_dry_run_leaves_row_unchanged(self) -> None:
        out = update_cashout_record(5, note="hold until Monday", ctx=None)
        self.assertTrue(out["refused"])
        self.assertFalse(out["applied"])
        self.assertEqual(out["actor"], "tester")
        self.assertEqual(out["proposed"], {"note": "hold until Monday"})
        self.assertEqual(out["current"]["id"], 5)
        self.assertEqual(out["current"]["status"], "active")
        self.assertIsNone(out["current"]["note"])
        self.assertIsNone(self._row().note)

    def test_confirm_applies_note(self) -> None:
        out = update_cashout_record(5, note="hold until Monday", confirm=True, ctx=None)
        self.assertTrue(out["applied"])
        self.assertFalse(out["refused"])
        self.assertEqual(out["actor"], "tester")
        self.assertIsNone(out["before"]["note"])
        self.assertEqual(out["after"]["note"], "hold until Monday")
        self.assertIn("note", out["message"])
        self.assertEqual(self._row().note, "hold until Monday")

    def test_confirm_applies_flags(self) -> None:
        out = update_cashout_record(5, sending=True, do_not_send=True, confirm=True)
        self.assertTrue(out["after"]["sending"])
        self.assertTrue(out["after"]["do_not_send"])
        row = self._row()
        self.assertTrue(row.sending)
        self.assertTrue(row.do_not_send)

    def test_missing_fields_is_tool_error(self) -> None:
        with self.assertRaises(ToolError) as cm:
            update_cashout_record(5, ctx=None)
        self.assertIn("at least one", str(cm.exception))
        self.assertIsNone(self._row().note)

    def test_unknown_record_is_tool_error(self) -> None:
        with self.assertRaises(ToolError) as cm:
            update_cashout_record(999, note="x")
        self.assertIn("not found", str(cm.exception))

    def test_audited_on_active_record_is_tool_error(self) -> None:
        # The service refuses audited=True until remaining is zero (ValueError).
        with self.assertRaises(ToolError):
            update_cashout_record(5, audited=True, confirm=True)
        self.assertFalse(self._row().audited)


class CreateExpenseTests(_WriteToolCase):
    tables = ["clubs", "expenses"]

    def _rows(self) -> list[Expense]:
        with self.factory() as session:
            return session.query(Expense).all()

    def test_dry_run_creates_nothing(self) -> None:
        out = create_expense(
            12.5, "Rakeback", "clubgto", description="  weekly ", ctx=None
        )
        self.assertTrue(out["refused"])
        self.assertFalse(out["applied"])
        self.assertIsNone(out["current"])
        proposed = out["proposed"]
        self.assertEqual(proposed["amount"], 12.5)
        self.assertEqual(proposed["expense_type"], "Rakeback")
        self.assertEqual(proposed["description"], "weekly")
        self.assertEqual(proposed["club_id"], 1)
        self.assertEqual(proposed["club_name"], "ClubGTO")
        self.assertTrue(proposed["pending"])
        self.assertIn("amounts are dollars", out["notes"])
        self.assertEqual(self._rows(), [])

    def test_confirm_creates_row(self) -> None:
        out = create_expense(
            12.5,
            "Rakeback",
            "1",
            expense_date="2026-07-01",
            pending=False,
            confirm=True,
            ctx=None,
        )
        self.assertTrue(out["applied"])
        self.assertIsNone(out["before"])
        after = out["after"]
        self.assertEqual(after["amount"], 12.5)
        self.assertEqual(after["expense_date"], "2026-07-01")
        self.assertEqual(after["club_name"], "ClubGTO")
        self.assertFalse(after["pending"])
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, after["id"])
        self.assertIsInstance(rows[0].amount, Decimal)
        self.assertEqual(rows[0].amount, Decimal("12.50"))
        self.assertEqual(rows[0].expense_type, "Rakeback")
        self.assertFalse(rows[0].pending)

    def test_zero_amount_is_tool_error(self) -> None:
        with self.assertRaises(ToolError) as cm:
            create_expense(0, "Rakeback", "clubgto", confirm=True)
        self.assertIn("amount", str(cm.exception))
        self.assertEqual(self._rows(), [])

    def test_blank_type_bad_date_and_bad_club_are_tool_errors(self) -> None:
        with self.assertRaises(ToolError):
            create_expense(5, "  ", "clubgto", confirm=True)
        with self.assertRaises(ToolError):
            create_expense(5, "Rakeback", "clubgto", expense_date="07/01/2026")
        with self.assertRaises(ToolError):
            create_expense(5, "Rakeback", "nope")
        self.assertEqual(self._rows(), [])


class VariantPauseTests(_WriteToolCase):
    tables = [
        "clubs",
        "club_payment_methods",
        "club_payment_tiers",
        "club_payment_tier_variants",
    ]

    def setUp(self) -> None:
        super().setUp()
        with self.factory() as session:
            session.add(
                ClubPaymentMethod(
                    id=10, club_id=1, direction="deposit", name="Zelle", slug="zelle"
                )
            )
            session.flush()
            session.add(ClubPaymentTier(id=20, method_id=10, label="Any"))
            session.flush()
            session.add(
                ClubPaymentTierVariant(
                    id=30, method_id=10, tier_id=20, label="Card A", tag="recipient-a"
                )
            )
            session.commit()

    def _variant(self) -> ClubPaymentTierVariant:
        with self.factory() as session:
            return session.get(ClubPaymentTierVariant, 30)

    def test_pause_dry_run(self) -> None:
        out = pause_variant(30, ctx=None)
        self.assertTrue(out["refused"])
        self.assertEqual(out["proposed"], {"action": "pause", "pause_hours": 48})
        current = out["current"]
        self.assertEqual(current["id"], 30)
        self.assertEqual(current["state"], "active")
        self.assertEqual(current["method_slug"], "zelle")
        self.assertEqual(current["tier_label"], "Any")
        self.assertEqual(current["club_id"], 1)
        row = self._variant()
        self.assertTrue(row.is_active)
        self.assertIsNone(row.paused_until)

    def test_pause_confirm(self) -> None:
        before = datetime.now(timezone.utc)
        out = pause_variant(30, confirm=True, ctx=None)
        self.assertTrue(out["applied"])
        self.assertEqual(out["before"]["state"], "active")
        self.assertEqual(out["after"]["state"], "paused")
        self.assertFalse(out["after"]["is_active"])
        self.assertIsNotNone(out["after"]["paused_until"])
        self.assertIn("Card A", out["message"])
        row = self._variant()
        self.assertFalse(row.is_active)
        paused_until = row.paused_until
        if paused_until.tzinfo is None:
            paused_until = paused_until.replace(tzinfo=timezone.utc)
        self.assertGreater(paused_until, before + timedelta(hours=47))
        self.assertLess(paused_until, before + timedelta(hours=49))

    def test_pause_already_off_is_tool_error(self) -> None:
        with self.factory() as session:
            session.get(ClubPaymentTierVariant, 30).is_active = False
            session.commit()
        with self.assertRaises(ToolError):
            pause_variant(30, confirm=True)

    def test_resume_confirm_returns_active(self) -> None:
        pause_variant(30, confirm=True)
        out = resume_variant(30, ctx=None)
        self.assertTrue(out["refused"])
        self.assertEqual(out["proposed"], {"action": "resume", "pause_hours": None})
        self.assertFalse(self._variant().is_active)

        out = resume_variant(30, confirm=True, ctx=None)
        self.assertTrue(out["applied"])
        self.assertEqual(out["before"]["state"], "paused")
        self.assertEqual(out["after"]["state"], "active")
        self.assertIsNone(out["after"]["paused_until"])
        row = self._variant()
        self.assertTrue(row.is_active)
        self.assertIsNone(row.paused_until)

    def test_resume_when_not_paused_is_tool_error(self) -> None:
        with self.assertRaises(ToolError):
            resume_variant(30, confirm=True)
        self.assertTrue(self._variant().is_active)

    def test_unknown_variant_is_tool_error(self) -> None:
        with self.assertRaises(ToolError):
            pause_variant(999)
        with self.assertRaises(ToolError):
            resume_variant(999, confirm=True)


class UpdateManualDepositRequestTests(_WriteToolCase):
    tables = ["clubs", "club_payment_methods", "manual_deposit_requests"]

    def setUp(self) -> None:
        super().setUp()
        with self.factory() as session:
            session.add(
                ClubPaymentMethod(
                    id=10,
                    club_id=1,
                    direction="deposit",
                    name="Pool Pay",
                    slug="pool-pay",
                    tracks_manual_requests=True,
                    deposit_limit=Decimal("10000"),
                )
            )
            session.flush()
            session.add(
                ManualDepositRequest(
                    id=7,
                    club_id=1,
                    method_id=10,
                    method_name="Pool Pay",
                    method_slug="pool-pay",
                    variant_name="TMT",
                    group_title="GTO / 8190-5287 / ThePirate343",
                    amount=Decimal("300.00"),
                    telegram_chat_id=-1001,
                    trade_record_checked=False,
                    created_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
                )
            )
            session.commit()

    def _row(self) -> ManualDepositRequest:
        with self.factory() as session:
            return session.get(ManualDepositRequest, 7)

    def test_dry_run(self) -> None:
        out = update_manual_deposit_request(7, trade_record_checked=True, ctx=None)
        self.assertTrue(out["refused"])
        self.assertEqual(out["proposed"], {"trade_record_checked": True})
        self.assertEqual(out["current"]["id"], 7)
        self.assertFalse(out["current"]["trade_record_checked"])
        self.assertEqual(out["current"]["amount"], 300.0)
        self.assertEqual(out["current"]["club"]["name"], "ClubGTO")
        self.assertIn("amounts are dollars", out["notes"])
        self.assertFalse(self._row().trade_record_checked)

    def test_confirm_marks_checked(self) -> None:
        out = update_manual_deposit_request(
            7, trade_record_checked=True, confirm=True, ctx=None
        )
        self.assertTrue(out["applied"])
        self.assertFalse(out["before"]["trade_record_checked"])
        self.assertTrue(out["after"]["trade_record_checked"])
        self.assertEqual(out["after"]["amount"], 300.0)
        self.assertTrue(self._row().trade_record_checked)

    def test_confirm_amount(self) -> None:
        out = update_manual_deposit_request(7, amount=350, confirm=True)
        self.assertEqual(out["after"]["amount"], 350.0)
        self.assertEqual(self._row().amount, Decimal("350.00"))

    def test_neither_field_is_tool_error(self) -> None:
        with self.assertRaises(ToolError) as cm:
            update_manual_deposit_request(7, ctx=None)
        self.assertIn("trade_record_checked", str(cm.exception))

    def test_bad_amount_and_unknown_request(self) -> None:
        with self.assertRaises(ToolError):
            update_manual_deposit_request(7, amount=0)
        with self.assertRaises(ToolError):
            update_manual_deposit_request(999, trade_record_checked=True)


class BindPaymentToGroupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, ENV, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.factory = make_sqlite_session_factory(["clubs", "venmo_payments"])
        mcp_db.session_factory = self.factory
        self.addCleanup(setattr, mcp_db, "session_factory", None)
        with self.factory() as session:
            session.add(Club(id=1, name="ClubGTO", telegram_user_id=1001))
            session.flush()
            session.add(
                VenmoPayment(
                    id=42,
                    method_owner="vaughn",
                    payer_name="Pat Payer",
                    amount_cents=5000,
                    venmo_handle="@pat-payer",
                    paid_at="2026-07-01T12:00:00Z",
                )
            )
            session.commit()
        self.bound = BoundGroup(
            telegram_chat_id=-1001, club_id=1, group_title="GTO / 8190-5287 / Pirate"
        )

    async def test_bad_method_is_tool_error(self) -> None:
        with self.assertRaises(ToolError) as cm:
            await bind_payment_to_group("wire", 42, "GTO / 8190-5287 / Pirate")
        self.assertIn("method must be one of", str(cm.exception))

    async def test_empty_title_is_tool_error(self) -> None:
        with self.assertRaises(ToolError) as cm:
            await bind_payment_to_group("venmo", 42, "   ")
        self.assertIn("group_title is required", str(cm.exception))

    async def test_unknown_payment_is_tool_error(self) -> None:
        with patch(
            "bot.services.venmo_payments.resolve_bound_group",
            return_value=BindResult(ok=True, bound_group=self.bound),
        ):
            with self.assertRaises(ToolError) as cm:
                await bind_payment_to_group("venmo", 999, "GTO / 8190-5287 / Pirate")
        self.assertIn("not found", str(cm.exception))

    async def test_unresolved_title_is_tool_error(self) -> None:
        with patch(
            "bot.services.venmo_payments.resolve_bound_group",
            return_value=BindResult(ok=False, error="No linked group matches."),
        ):
            with self.assertRaises(ToolError) as cm:
                await bind_payment_to_group("venmo", 42, "GTO / 0000-0000 / Nobody")
        self.assertIn("No linked group matches", str(cm.exception))

    async def test_dry_run(self) -> None:
        bind = AsyncMock(return_value=BindResult(ok=True, bound_group=self.bound))
        with (
            patch(
                "bot.services.venmo_payments.resolve_bound_group",
                return_value=BindResult(ok=True, bound_group=self.bound),
            ),
            patch("bot.services.venmo_payments.bind_venmo_payment_by_id", bind),
        ):
            out = await bind_payment_to_group(
                "venmo", 42, "GTO / 8190-5287 / Pirate", ctx=None
            )
        self.assertTrue(out["refused"])
        self.assertFalse(out["applied"])
        self.assertEqual(out["actor"], "tester")
        self.assertEqual(
            out["proposed"]["bind_to"],
            {
                "telegram_chat_id": -1001,
                "club_id": 1,
                "group_title": "GTO / 8190-5287 / Pirate",
            },
        )
        current = out["current"]
        self.assertEqual(current["id"], 42)
        self.assertEqual(current["method"], "venmo")
        self.assertEqual(current["amount_cents"], 5000)
        self.assertIsNone(current["telegram_chat_id"])
        bind.assert_not_awaited()

    async def test_confirm_awaits_bind(self) -> None:
        bind = AsyncMock(return_value=BindResult(ok=True, bound_group=self.bound))
        with (
            patch(
                "bot.services.venmo_payments.resolve_bound_group",
                return_value=BindResult(ok=True, bound_group=self.bound),
            ),
            patch("bot.services.venmo_payments.bind_venmo_payment_by_id", bind),
        ):
            out = await bind_payment_to_group(
                "Venmo", 42, " GTO / 8190-5287 / Pirate ", confirm=True, ctx=None
            )
        bind.assert_awaited_once_with(
            payment_id=42, group_title_input="GTO / 8190-5287 / Pirate"
        )
        self.assertTrue(out["applied"])
        self.assertFalse(out["refused"])
        self.assertEqual(out["before"]["id"], 42)
        self.assertEqual(out["after"]["id"], 42)
        self.assertIn("GTO / 8190-5287 / Pirate", out["message"])
        self.assertTrue(any("bound_via" in note for note in out["notes"]))

    async def test_confirm_bind_failure_is_tool_error(self) -> None:
        bind = AsyncMock(return_value=BindResult(ok=False, error="Already bound."))
        with (
            patch(
                "bot.services.venmo_payments.resolve_bound_group",
                return_value=BindResult(ok=True, bound_group=self.bound),
            ),
            patch("bot.services.venmo_payments.bind_venmo_payment_by_id", bind),
        ):
            with self.assertRaises(ToolError) as cm:
                await bind_payment_to_group(
                    "venmo", 42, "GTO / 8190-5287 / Pirate", confirm=True
                )
        self.assertIn("Already bound", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
