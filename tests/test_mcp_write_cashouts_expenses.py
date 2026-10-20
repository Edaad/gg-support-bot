"""Cashout record / destination / send and expense write tools."""

from __future__ import annotations

import os
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from mcp.server.mcpserver.exceptions import ToolError

import mcp_server.db as mcp_db
from db.models import (
    Club,
    ClubPaymentMethod,
    ClubPaymentSubOption,
    Expense,
    StaffCashoutMoneySend,
    StaffCashoutPayment,
    StaffCashoutRecord,
    StaffCashoutSendProof,
)
from mcp_server.tools.cashouts import (
    add_cashout_destination,
    add_cashout_send,
    create_cashout_record,
    delete_cashout_record,
    remove_cashout_destination,
    remove_cashout_send,
    update_cashout_destination,
    update_cashout_record,
    update_cashout_send,
)
from mcp_server.tools.expenses import delete_expense, update_expense
from tests.support.sqlite_db import get_db_for, make_sqlite_session_factory

ENV = {"MCP_API_TOKENS": "dev:" + "x" * 32, "MCP_ACTOR": "tester"}

CASHOUT_TABLES = [
    "clubs",
    "cashier_cashout_jobs",
    "staff_cashout_records",
    "staff_cashout_payments",
    "staff_cashout_money_sends",
    "staff_cashout_send_proofs",
    "club_payment_methods",
    "club_payment_sub_options",
    "expenses",
]

TITLE = "GTO / 8190-5287 / ThePirate343"
PUSHOVER = "bot.services.staff_cashout_pushover.notify_cashout_pushover_sync"


class _Case(unittest.TestCase):
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
            "bot.services.club",
            "bot.services.club_payment_v2",
        ):
            patcher = patch(f"{module}.get_db", get_db_for(self.factory))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.pushover = MagicMock()
        pushover = patch(PUSHOVER, self.pushover)
        pushover.start()
        self.addCleanup(pushover.stop)
        with self.factory() as session:
            session.add(Club(id=1, name="ClubGTO", telegram_user_id=1001))
            session.add(Club(id=2, name="Round Table", telegram_user_id=1002))
            session.commit()


class _CashoutCase(_Case):
    tables = CASHOUT_TABLES

    def setUp(self) -> None:
        super().setUp()
        with self.factory() as session:
            session.add(
                ClubPaymentMethod(
                    id=10, club_id=1, direction="cashout", name="Zelle", slug="zelle"
                )
            )
            session.add(
                ClubPaymentMethod(
                    id=11,
                    club_id=1,
                    direction="cashout",
                    name="Crypto",
                    slug="crypto",
                    has_sub_options=True,
                )
            )
            session.flush()
            session.add(
                ClubPaymentSubOption(id=21, method_id=11, name="USDT", slug="usdt")
            )
            session.add(
                StaffCashoutRecord(
                    id=5,
                    club_id=1,
                    chat_id=None,
                    group_title=TITLE,
                    gg_player_id="8190-5287",
                    amount=Decimal("250.00"),
                    trigger="dashboard",
                    tracks_money_sent=True,
                )
            )
            session.flush()
            session.add(
                StaffCashoutPayment(
                    id=50,
                    cashout_record_id=5,
                    method_display_name="Venmo",
                    payout_details="https://venmo.com/u/pirate",
                    sort_order=0,
                )
            )
            session.commit()

    def _record(self, record_id: int = 5) -> StaffCashoutRecord | None:
        with self.factory() as session:
            return session.get(StaffCashoutRecord, record_id)

    def _payments(self, record_id: int = 5) -> list[StaffCashoutPayment]:
        with self.factory() as session:
            return (
                session.query(StaffCashoutPayment)
                .filter_by(cashout_record_id=record_id)
                .order_by(StaffCashoutPayment.sort_order)
                .all()
            )

    def _sends(self, record_id: int = 5) -> list[StaffCashoutMoneySend]:
        with self.factory() as session:
            return (
                session.query(StaffCashoutMoneySend)
                .filter_by(cashout_record_id=record_id)
                .order_by(StaffCashoutMoneySend.id)
                .all()
            )

    def _add_send(self, amount: str, send_id: int = 70, proof: bool = False) -> None:
        with self.factory() as session:
            row = StaffCashoutMoneySend(
                id=send_id,
                cashout_record_id=5,
                sender_name="Aiden",
                amount=Decimal(amount),
                method_display_name="Venmo",
                has_proof=proof,
            )
            session.add(row)
            session.flush()
            if proof:
                session.add(
                    StaffCashoutSendProof(
                        money_send_id=send_id,
                        filename="shot.png",
                        content_type="image/png",
                        content=b"\x89PNG",
                    )
                )
            session.commit()


class CreateCashoutRecordTests(_CashoutCase):
    def _records(self) -> list[StaffCashoutRecord]:
        with self.factory() as session:
            return session.query(StaffCashoutRecord).all()

    def test_dry_run_validates_and_creates_nothing(self) -> None:
        out = create_cashout_record(
            "clubgto",
            " RT / 2427-3267 / Samin ",
            372.34,
            [{"method_display_name": "Venmo", "payout_details": "@samin"}],
            note="  first cashout ",
            ctx=None,
        )
        self.assertTrue(out["refused"])
        self.assertFalse(out["applied"])
        self.assertEqual(out["actor"], "tester")
        self.assertIsNone(out["current"])
        proposed = out["proposed"]
        self.assertEqual(proposed["club_id"], 1)
        self.assertEqual(proposed["club_name"], "ClubGTO")
        self.assertEqual(proposed["group_title"], "RT / 2427-3267 / Samin")
        self.assertEqual(proposed["gg_player_id"], "2427-3267")
        self.assertEqual(proposed["amount"], 372.0)
        self.assertEqual(proposed["requested_amount"], 372.34)
        self.assertNotIn("round_up_expense", proposed)
        self.assertTrue(any("rounded down" in n for n in out["notes"]))
        self.assertEqual(proposed["note"], "first cashout")
        self.assertEqual(proposed["trigger"], "dashboard")
        self.assertEqual(len(proposed["payments"]), 1)
        dest = proposed["payments"][0]
        self.assertEqual(dest["method_display_name"], "Venmo")
        self.assertIsNone(dest["payment_method_id"])
        # payout_details is PII and masked by the envelope
        self.assertEqual(dest["payout_details"], "[redacted]")
        self.assertTrue(any("Pushover" in n for n in out["notes"]))
        self.assertEqual(len(self._records()), 1)
        self.pushover.assert_not_called()

    def test_confirm_creates_record_destinations_rounds_down_and_notifies(
        self,
    ) -> None:
        out = create_cashout_record(
            "1",
            "RT / 2427-3267 / Samin",
            372.34,
            [
                {"method_display_name": "Venmo", "payout_details": "@samin"},
                {
                    "payment_method_id": 10,
                    "payout_details": "samin@x.com",
                    "amount": 100,
                },
            ],
            confirm=True,
            ctx=None,
        )
        self.assertTrue(out["applied"])
        self.assertIsNone(out["before"])
        after = out["after"]
        new_id = after["id"]
        self.assertNotEqual(new_id, 5)
        self.assertEqual(after["amount"], 372.0)
        self.assertEqual(after["status"], "active")
        self.assertEqual(after["club_name"], "ClubGTO")
        self.assertEqual(after["gg_player_id"], "2427-3267")
        self.assertTrue(after["tracks_money_sent"])
        self.assertEqual(len(after["payments"]), 2)
        self.assertEqual(after["payments"][1]["payment_method_id"], 10)
        self.assertEqual(after["payments"][1]["method_display_name"], "Zelle")
        self.assertEqual(after["payments"][1]["amount"], 100.0)
        self.pushover.assert_called_once()
        self.assertEqual(self.pushover.call_args.args[0], new_id)

        row = self._record(new_id)
        self.assertEqual(row.amount, Decimal("372"))
        self.assertEqual(row.trigger, "dashboard")
        self.assertIsNone(row.chat_id)
        payments = self._payments(new_id)
        self.assertEqual(len(payments), 2)
        # A bare Venmo handle is stored as its payment link.
        self.assertTrue(payments[0].payout_details.startswith("https://"))
        self.assertIn("samin", payments[0].payout_details)
        self.assertEqual(payments[1].payout_details, "samin@x.com")
        with self.factory() as session:
            self.assertEqual(session.query(Expense).count(), 0)

    def test_whole_dollar_amount_is_unchanged(self) -> None:
        out = create_cashout_record(
            "clubgto",
            "GTO / 1-2 / X",
            200,
            [{"method_display_name": "Chips"}],
            confirm=True,
        )
        self.assertEqual(out["after"]["amount"], 200.0)
        self.assertFalse(any("rounded down" in n for n in out["notes"]))
        with self.factory() as session:
            self.assertEqual(session.query(Expense).count(), 0)

    def test_amount_under_a_dollar_is_refused(self) -> None:
        with self.assertRaises(ToolError) as cm:
            create_cashout_record(
                "clubgto", "GTO / 1-2 / X", 0.5, [{"method_display_name": "Chips"}]
            )
        self.assertIn("at least 1 dollar", str(cm.exception))

    def test_validation_errors(self) -> None:
        dest = [{"method_display_name": "Venmo", "payout_details": "@x"}]
        with self.assertRaises(ToolError) as cm:
            create_cashout_record("clubgto", "GTO / 1-2 / X", 100, [])
        self.assertIn("payments", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            create_cashout_record("clubgto", "   ", 100, dest)
        self.assertIn("group_title", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            create_cashout_record("clubgto", "GTO / 1-2 / X", 0, dest)
        self.assertIn("amount", str(cm.exception))
        with self.assertRaises(ToolError):
            create_cashout_record("nope", "GTO / 1-2 / X", 100, dest)
        # catalog method without payout details
        with self.assertRaises(ToolError) as cm:
            create_cashout_record(
                "clubgto", "GTO / 1-2 / X", 100, [{"payment_method_id": 10}]
            )
        self.assertIn("Payout details", str(cm.exception))
        # custom method without a name
        with self.assertRaises(ToolError) as cm:
            create_cashout_record(
                "clubgto", "GTO / 1-2 / X", 100, [{"payout_details": "@x"}]
            )
        self.assertIn("Custom method name", str(cm.exception))
        # unknown catalog method / unknown keys
        with self.assertRaises(ToolError):
            create_cashout_record(
                "clubgto",
                "GTO / 1-2 / X",
                100,
                [{"payment_method_id": 999, "payout_details": "x"}],
            )
        with self.assertRaises(ToolError) as cm:
            create_cashout_record(
                "clubgto", "GTO / 1-2 / X", 100, [{"display": "Venmo"}]
            )
        self.assertIn("unknown keys", str(cm.exception))
        self.assertEqual(len(self._records()), 1)
        self.pushover.assert_not_called()


class DeleteCashoutRecordTests(_CashoutCase):
    def test_dry_run_shows_full_record(self) -> None:
        self._add_send("50")
        out = delete_cashout_record(5, ctx=None)
        self.assertTrue(out["refused"])
        self.assertEqual(out["proposed"], {"delete": True})
        current = out["current"]
        self.assertEqual(current["id"], 5)
        self.assertEqual(current["group_title"], TITLE)
        self.assertEqual(current["club_name"], "ClubGTO")
        self.assertEqual(len(current["payments"]), 1)
        self.assertEqual(len(current["sends"]), 1)
        self.assertTrue(
            any("1 destination(s) and 1 money-sent" in n for n in out["notes"])
        )
        self.assertIsNotNone(self._record())

    def test_confirm_deletes_record_and_children(self) -> None:
        self._add_send("50", proof=True)
        out = delete_cashout_record(5, confirm=True, ctx=None)
        self.assertTrue(out["applied"])
        self.assertEqual(out["before"]["id"], 5)
        self.assertIsNone(out["after"])
        self.assertIn(TITLE, out["message"])
        self.assertIsNone(self._record())
        self.assertEqual(self._payments(), [])
        self.assertEqual(self._sends(), [])
        with self.factory() as session:
            self.assertEqual(session.query(StaffCashoutSendProof).count(), 0)

    def test_unknown_record_is_tool_error(self) -> None:
        with self.assertRaises(ToolError) as cm:
            delete_cashout_record(999, confirm=True)
        self.assertIn("not found", str(cm.exception))


class UpdateCashoutRecordAmountTitleTests(_CashoutCase):
    def test_dry_run_proposes_amount_and_title(self) -> None:
        out = update_cashout_record(
            5, amount=300, group_title="GTO / 1111-2222 / Other", ctx=None
        )
        self.assertTrue(out["refused"])
        self.assertEqual(
            out["proposed"], {"amount": 300.0, "group_title": "GTO / 1111-2222 / Other"}
        )
        row = self._record()
        self.assertEqual(row.amount, Decimal("250.00"))
        self.assertEqual(row.group_title, TITLE)

    def test_confirm_changes_amount_and_reparses_player_id(self) -> None:
        out = update_cashout_record(
            5, amount=300, group_title=" GTO / 1111-2222 / Other ", confirm=True
        )
        self.assertTrue(out["applied"])
        self.assertEqual(out["after"]["amount"], 300.0)
        self.assertEqual(out["after"]["remaining"], 300.0)
        self.assertEqual(out["after"]["group_title"], "GTO / 1111-2222 / Other")
        self.assertEqual(out["after"]["gg_player_id"], "1111-2222")
        row = self._record()
        self.assertEqual(row.amount, Decimal("300"))
        self.assertEqual(row.gg_player_id, "1111-2222")

    def test_note_only_still_works(self) -> None:
        out = update_cashout_record(5, note="hold", confirm=True)
        self.assertEqual(out["after"]["note"], "hold")
        self.assertEqual(out["after"]["amount"], 250.0)

    def test_amount_on_cleared_record_is_conflict(self) -> None:
        self._add_send("250")
        with self.assertRaises(ToolError) as cm:
            update_cashout_record(5, amount=300, confirm=True)
        self.assertIn("conflict", str(cm.exception))
        self.assertEqual(self._record().amount, Decimal("250.00"))

    def test_bad_amount_and_blank_title_are_tool_errors(self) -> None:
        with self.assertRaises(ToolError):
            update_cashout_record(5, amount=0)
        with self.assertRaises(ToolError):
            update_cashout_record(5, amount=-5, confirm=True)
        with self.assertRaises(ToolError):
            update_cashout_record(5, group_title="   ")
        with self.assertRaises(ToolError) as cm:
            update_cashout_record(5)
        self.assertIn("at least one", str(cm.exception))
        with self.assertRaises(ToolError):
            update_cashout_record(999, amount=10)


class DestinationTests(_CashoutCase):
    def test_add_dry_run(self) -> None:
        out = add_cashout_destination(
            5, payment_method_id=10, payout_details="pirate@x.com", amount=50, ctx=None
        )
        self.assertTrue(out["refused"])
        self.assertEqual(out["current"]["id"], 5)
        proposed = out["proposed"]
        self.assertEqual(proposed["payment_method_id"], 10)
        self.assertEqual(proposed["method_display_name"], "Zelle")
        self.assertEqual(proposed["amount"], 50.0)
        self.assertIn("Zelle", out["message"])
        self.assertEqual(len(self._payments()), 1)

    def test_add_confirm_custom(self) -> None:
        out = add_cashout_destination(
            5, method_display_name="Cash App", payout_details="$pirate", confirm=True
        )
        self.assertTrue(out["applied"])
        self.assertEqual(len(out["before"]["payments"]), 1)
        self.assertEqual(len(out["after"]["payments"]), 2)
        payments = self._payments()
        self.assertEqual(payments[1].method_display_name, "Cash App")
        self.assertEqual(payments[1].payout_details, "$pirate")
        self.assertEqual(payments[1].sort_order, 1)
        self.assertIsNone(payments[1].payment_method_id)

    def test_add_confirm_catalog_with_sub_option(self) -> None:
        out = add_cashout_destination(
            5,
            payment_method_id=11,
            payment_sub_option_id=21,
            payout_details="0xabc",
            confirm=True,
        )
        dest = out["after"]["payments"][1]
        self.assertEqual(dest["payment_method_id"], 11)
        self.assertEqual(dest["payment_sub_option_id"], 21)
        self.assertEqual(dest["method_display_name"], "Crypto / USDT")

    def test_add_validation(self) -> None:
        with self.assertRaises(ToolError) as cm:
            add_cashout_destination(5, payout_details="x")
        self.assertIn("payment_method_id or method_display_name", str(cm.exception))
        with self.assertRaises(ToolError):
            add_cashout_destination(5, payment_method_id=10)  # needs payout details
        with self.assertRaises(ToolError):
            add_cashout_destination(5, payment_method_id=11, payout_details="0xabc")
        with self.assertRaises(ToolError):
            add_cashout_destination(5, method_display_name="Chips", amount=0)
        with self.assertRaises(ToolError):
            add_cashout_destination(999, method_display_name="Chips", confirm=True)
        self.assertEqual(len(self._payments()), 1)

    def test_update_dry_run_and_confirm(self) -> None:
        out = update_cashout_destination(
            5, 50, payout_details="https://venmo.com/u/other", ctx=None
        )
        self.assertTrue(out["refused"])
        self.assertEqual(out["current"]["record"]["id"], 5)
        self.assertEqual(out["current"]["destination"]["id"], 50)
        self.assertEqual(out["proposed"]["method_display_name"], "Venmo")
        self.assertEqual(
            self._payments()[0].payout_details, "https://venmo.com/u/pirate"
        )

        out = update_cashout_destination(
            5, 50, payout_details="https://venmo.com/u/other", amount=25, confirm=True
        )
        self.assertTrue(out["applied"])
        self.assertEqual(out["after"]["payments"][0]["amount"], 25.0)
        row = self._payments()[0]
        self.assertEqual(row.payout_details, "https://venmo.com/u/other")
        self.assertEqual(row.amount, Decimal("25"))

    def test_update_switches_to_catalog_method(self) -> None:
        out = update_cashout_destination(
            5, 50, payment_method_id=10, payout_details="pirate@x.com", confirm=True
        )
        dest = out["after"]["payments"][0]
        self.assertEqual(dest["payment_method_id"], 10)
        self.assertEqual(dest["method_display_name"], "Zelle")

    def test_update_validation_and_not_found(self) -> None:
        with self.assertRaises(ToolError) as cm:
            update_cashout_destination(5, 50)
        self.assertIn("at least one", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            update_cashout_destination(5, 999, amount=10)
        self.assertIn("not found", str(cm.exception))
        with self.assertRaises(ToolError):
            update_cashout_destination(999, 50, amount=10)
        # Switching to a catalog method without payout details fails validation.
        with self.factory() as session:
            session.get(StaffCashoutPayment, 50).payout_details = None
            session.commit()
        with self.assertRaises(ToolError):
            update_cashout_destination(5, 50, payment_method_id=10, confirm=True)

    def test_remove_dry_run_and_confirm(self) -> None:
        out = remove_cashout_destination(5, 50, ctx=None)
        self.assertTrue(out["refused"])
        self.assertEqual(out["proposed"], {"delete": True})
        self.assertEqual(out["current"]["destination"]["method_display_name"], "Venmo")
        self.assertEqual(len(self._payments()), 1)

        out = remove_cashout_destination(5, 50, confirm=True)
        self.assertTrue(out["applied"])
        self.assertEqual(out["after"]["payments"], [])
        self.assertEqual(self._payments(), [])
        self.assertIsNotNone(self._record())

    def test_remove_not_found(self) -> None:
        with self.assertRaises(ToolError):
            remove_cashout_destination(5, 999, confirm=True)
        with self.assertRaises(ToolError):
            remove_cashout_destination(999, 50)


class SendTests(_CashoutCase):
    def test_add_dry_run_defaults_sender_to_actor(self) -> None:
        out = add_cashout_send(5, 100, "Venmo", ctx=None)
        self.assertTrue(out["refused"])
        self.assertEqual(out["current"]["id"], 5)
        proposed = out["proposed"]
        self.assertEqual(proposed["sender_name"], "t***")  # masked PII
        self.assertEqual(proposed["amount"], 100.0)
        self.assertEqual(proposed["method_display_name"], "Venmo")
        self.assertFalse(proposed["notify_player"])
        self.assertEqual(proposed["sent_after"], 100.0)
        self.assertEqual(proposed["remaining_after"], 150.0)
        self.assertTrue(any("balance cards" in n for n in out["notes"]))
        self.assertEqual(self._sends(), [])

    def test_add_confirm_updates_ledger(self) -> None:
        out = add_cashout_send(5, 100, "Venmo", sender_name="Aiden", confirm=True)
        self.assertTrue(out["applied"])
        self.assertEqual(out["before"]["status"], "active")
        self.assertEqual(out["after"]["sent"], 100.0)
        self.assertEqual(out["after"]["remaining"], 150.0)
        self.assertEqual(out["after"]["status"], "active")
        self.assertIn("active", out["message"])
        sends = self._sends()
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0].sender_name, "Aiden")
        self.assertEqual(sends[0].amount, Decimal("100"))
        self.assertFalse(sends[0].notify_player)
        self.assertIsNone(sends[0].notify_status)
        self.assertFalse(sends[0].has_proof)

        out = add_cashout_send(5, 150, "Zelle", sender_name="Aiden", confirm=True)
        self.assertEqual(out["after"]["status"], "cleared")
        self.assertEqual(out["after"]["remaining"], 0.0)
        # Not linked to a chat: no owed-pin clear is queued.
        self.assertIsNone(self._record().owed_clear_status)

    def test_notify_ignored_when_not_connected(self) -> None:
        out = add_cashout_send(5, 10, "Venmo", notify=True, confirm=True)
        self.assertTrue(any("notify ignored" in n for n in out["notes"]))
        self.assertFalse(out["after"]["sends"][0]["notify_player"])
        self.assertFalse(self._sends()[0].notify_player)

    def test_notify_queued_when_connected(self) -> None:
        with self.factory() as session:
            session.get(StaffCashoutRecord, 5).chat_id = -1001
            session.commit()
        with patch(
            "bot.services.staff_cashout_records.record_chat_connected",
            return_value=True,
        ):
            dry = add_cashout_send(5, 250, "Venmo", notify=True)
            self.assertTrue(dry["proposed"]["notify_player"])
            out = add_cashout_send(5, 250, "Venmo", notify=True, confirm=True)
        send = out["after"]["sends"][0]
        self.assertTrue(send["notify_player"])
        self.assertEqual(send["notify_status"], "pending")
        # Fully covered and linked to a chat: owed-pin clear queued for the worker.
        self.assertEqual(self._record().owed_clear_status, "pending")

    def test_proof_link_rules(self) -> None:
        with self.assertRaises(ToolError) as cm:
            add_cashout_send(5, 10, "Venmo", proof_link="https://x.y/tx", confirm=True)
        self.assertIn("crypto", str(cm.exception).lower())
        with self.assertRaises(ToolError):
            add_cashout_send(5, 10, "Crypto", proof_link="ftp://x", confirm=True)
        self.assertEqual(self._sends(), [])
        out = add_cashout_send(
            5, 10, "Crypto", proof_link="https://tronscan.org/tx/abc", confirm=True
        )
        self.assertEqual(self._sends()[0].proof_link, "https://tronscan.org/tx/abc")
        self.assertEqual(out["after"]["sends"][0]["proof_link"], "[redacted]")

    def test_add_validation(self) -> None:
        with self.assertRaises(ToolError):
            add_cashout_send(5, 0, "Venmo")
        with self.assertRaises(ToolError):
            add_cashout_send(5, 10, "  ")
        with self.assertRaises(ToolError):
            add_cashout_send(999, 10, "Venmo", confirm=True)
        self.assertEqual(self._sends(), [])

    def test_update_send(self) -> None:
        self._add_send("50")
        out = update_cashout_send(5, 70, amount=75, sender_name="Edaad", ctx=None)
        self.assertTrue(out["refused"])
        self.assertEqual(out["current"]["send"]["id"], 70)
        self.assertEqual(out["current"]["record"]["id"], 5)
        self.assertEqual(out["proposed"]["amount"], 75.0)
        self.assertEqual(self._sends()[0].amount, Decimal("50"))

        out = update_cashout_send(
            5,
            70,
            amount=75,
            sender_name="Edaad",
            method_display_name="Zelle",
            confirm=True,
        )
        self.assertTrue(out["applied"])
        self.assertEqual(out["after"]["sent"], 75.0)
        self.assertEqual(out["after"]["sends"][0]["method_display_name"], "Zelle")
        row = self._sends()[0]
        self.assertEqual(row.amount, Decimal("75"))
        self.assertEqual(row.sender_name, "Edaad")
        self.assertEqual(row.method_display_name, "Zelle")

    def test_update_send_validation_and_not_found(self) -> None:
        self._add_send("50")
        with self.assertRaises(ToolError) as cm:
            update_cashout_send(5, 70)
        self.assertIn("at least one", str(cm.exception))
        with self.assertRaises(ToolError):
            update_cashout_send(5, 70, amount=0)
        with self.assertRaises(ToolError):
            update_cashout_send(5, 70, sender_name="  ")
        with self.assertRaises(ToolError):
            update_cashout_send(5, 70, method_display_name="  ")
        with self.assertRaises(ToolError) as cm:
            update_cashout_send(5, 999, amount=10)
        self.assertIn("not found", str(cm.exception))
        with self.assertRaises(ToolError):
            update_cashout_send(999, 70, amount=10)

    def test_remove_send(self) -> None:
        self._add_send("250", proof=True)
        self.assertEqual(self._record().owed_clear_status, None)
        out = remove_cashout_send(5, 70, ctx=None)
        self.assertTrue(out["refused"])
        self.assertEqual(out["current"]["send"]["amount"], 250.0)
        self.assertTrue(out["current"]["send"]["has_proof"])
        self.assertNotIn("content", out["current"]["send"])
        self.assertEqual(len(self._sends()), 1)

        out = remove_cashout_send(5, 70, confirm=True)
        self.assertTrue(out["applied"])
        self.assertEqual(out["before"]["status"], "cleared")
        self.assertEqual(out["after"]["status"], "active")
        self.assertEqual(out["after"]["sends"], [])
        self.assertEqual(self._sends(), [])
        with self.factory() as session:
            self.assertEqual(session.query(StaffCashoutSendProof).count(), 0)

    def test_remove_send_not_found(self) -> None:
        with self.assertRaises(ToolError):
            remove_cashout_send(5, 999, confirm=True)
        with self.assertRaises(ToolError):
            remove_cashout_send(999, 70)


class ExpenseWriteTests(_Case):
    tables = ["clubs", "expenses"]

    def setUp(self) -> None:
        super().setUp()
        with self.factory() as session:
            session.add(
                Expense(
                    id=8,
                    amount=Decimal("40.00"),
                    expense_type="Rakeback",
                    description="weekly",
                    club_id=1,
                    expense_date=date(2026, 7, 1),
                    pending=True,
                )
            )
            session.commit()

    def _row(self) -> Expense | None:
        with self.factory() as session:
            return session.get(Expense, 8)

    def test_update_dry_run(self) -> None:
        out = update_expense(8, amount=45.5, club="round-table", ctx=None)
        self.assertTrue(out["refused"])
        self.assertEqual(out["actor"], "tester")
        self.assertEqual(out["current"]["id"], 8)
        self.assertEqual(out["current"]["amount"], 40.0)
        self.assertEqual(out["current"]["club_name"], "ClubGTO")
        self.assertEqual(
            out["proposed"], {"amount": 45.5, "club_id": 2, "club_name": "Round Table"}
        )
        self.assertIn("amounts are dollars", out["notes"])
        row = self._row()
        self.assertEqual(row.amount, Decimal("40.00"))
        self.assertEqual(row.club_id, 1)

    def test_update_confirm(self) -> None:
        out = update_expense(
            8,
            amount=45.5,
            expense_type="  Bonus ",
            description="   ",
            club="2",
            expense_date="2026-07-02",
            pending=False,
            confirm=True,
            ctx=None,
        )
        self.assertTrue(out["applied"])
        self.assertEqual(out["before"]["description"], "weekly")
        after = out["after"]
        self.assertEqual(after["amount"], 45.5)
        self.assertEqual(after["expense_type"], "Bonus")
        self.assertIsNone(after["description"])
        self.assertEqual(after["club_id"], 2)
        self.assertEqual(after["club_name"], "Round Table")
        self.assertEqual(after["expense_date"], "2026-07-02")
        self.assertFalse(after["pending"])
        self.assertIsNotNone(after["updated_at"])
        row = self._row()
        self.assertEqual(row.amount, Decimal("45.50"))
        self.assertEqual(row.expense_type, "Bonus")
        self.assertIsNone(row.description)
        self.assertEqual(row.club_id, 2)
        self.assertFalse(row.pending)

    def test_update_single_field_keeps_others(self) -> None:
        out = update_expense(8, pending=False, confirm=True)
        after = out["after"]
        self.assertFalse(after["pending"])
        self.assertEqual(after["amount"], 40.0)
        self.assertEqual(after["description"], "weekly")
        self.assertEqual(after["club_name"], "ClubGTO")

    def test_update_validation_and_not_found(self) -> None:
        with self.assertRaises(ToolError) as cm:
            update_expense(8)
        self.assertIn("at least one", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            update_expense(8, amount=0, confirm=True)
        self.assertIn("zero", str(cm.exception))
        with self.assertRaises(ToolError):
            update_expense(8, expense_type="   ", confirm=True)
        with self.assertRaises(ToolError):
            update_expense(8, expense_date="07/02/2026")
        with self.assertRaises(ToolError):
            update_expense(8, club="nope")
        with self.assertRaises(ToolError):
            update_expense(8, club="999")
        with self.assertRaises(ToolError) as cm:
            update_expense(999, pending=False)
        self.assertIn("not found", str(cm.exception))
        row = self._row()
        self.assertEqual(row.amount, Decimal("40.00"))
        self.assertTrue(row.pending)

    def test_delete_dry_run_shows_row(self) -> None:
        out = delete_expense(8, ctx=None)
        self.assertTrue(out["refused"])
        self.assertEqual(out["proposed"], {"delete": True})
        current = out["current"]
        self.assertEqual(current["id"], 8)
        self.assertEqual(current["amount"], 40.0)
        self.assertEqual(current["expense_type"], "Rakeback")
        self.assertEqual(current["club_name"], "ClubGTO")
        self.assertIsNotNone(self._row())

    def test_delete_confirm(self) -> None:
        out = delete_expense(8, confirm=True, ctx=None)
        self.assertTrue(out["applied"])
        self.assertEqual(out["before"]["id"], 8)
        self.assertIsNone(out["after"])
        self.assertIn("Rakeback", out["message"])
        self.assertIsNone(self._row())

    def test_delete_not_found(self) -> None:
        with self.assertRaises(ToolError) as cm:
            delete_expense(999, confirm=True)
        self.assertIn("not found", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
