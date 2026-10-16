"""Write tools for bindings, quick links, outbound sends, balances and deposit alerts."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.server.mcpserver.exceptions import ToolError

import mcp_server.db as mcp_db
from bot.services.deposit_method_alerts import WeekStats
from db.models import (
    AccountBalanceSettings,
    Club,
    ClubPaymentMethod,
    ClubPaymentTier,
    ClubPaymentTierVariant,
    DepositMethodAlert,
    Group,
    GroupDepositDestinationStickiness,
    GroupPaymentMethodBinding,
    OutboundSend,
    PaymentMethodBindAttempt,
    PaymentQuickLink,
)
from mcp_server.tools.ops import (
    create_deposit_method_alert,
    delete_deposit_method_alert,
    update_deposit_method_alert,
)
from mcp_server.tools.payments import (
    create_payment_quick_link,
    delete_payment_quick_link,
    unbind_payment,
    update_payment_quick_link,
)
from mcp_server.tools.sends_balances import (
    delete_outbound_send,
    record_outbound_send,
    update_account_balance_settings,
    update_outbound_send,
)
from tests.support.sqlite_db import get_db_for, make_sqlite_session_factory

ENV = {"MCP_API_TOKENS": "dev:" + "x" * 32, "MCP_ACTOR": "tester"}
VARIANT_TABLES = [
    "club_payment_methods",
    "club_payment_tiers",
    "club_payment_tier_variants",
]


def _seed_variant(
    session, *, club_id: int, slug: str, tag: str, method_id: int, variant_id: int
) -> None:
    session.add(
        ClubPaymentMethod(
            id=method_id, club_id=club_id, direction="deposit", name=slug, slug=slug
        )
    )
    session.flush()
    tier_id = method_id * 10
    session.add(ClubPaymentTier(id=tier_id, method_id=method_id, label="Any"))
    session.flush()
    session.add(
        ClubPaymentTierVariant(
            id=variant_id, method_id=method_id, tier_id=tier_id, label=tag, tag=tag
        )
    )
    session.flush()


class _Case(unittest.TestCase):
    tables: list[str] = []
    club_name = "ClubGTO"

    def setUp(self) -> None:
        env = patch.dict(os.environ, ENV, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.factory = make_sqlite_session_factory(self.tables)
        mcp_db.session_factory = self.factory
        self.addCleanup(setattr, mcp_db, "session_factory", None)
        with self.factory() as session:
            session.add(Club(id=1, name=self.club_name, telegram_user_id=1001))
            session.commit()


# ── unbind_payment ───────────────────────────────────────────────────────────


class UnbindPaymentTests(_Case):
    tables = [
        "clubs",
        "groups",
        "support_group_chats",
        *VARIANT_TABLES,
        "venmo_payments",
        "zelle_payments",
        "cashapp_payments",
        "paypal_payments",
        "payment_method_bind_attempts",
        "group_payment_method_bindings",
        "group_deposit_destination_stickiness",
    ]

    def setUp(self) -> None:
        super().setUp()
        patcher = patch(
            "bot.services.payment_method_binding.get_db", get_db_for(self.factory)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.factory() as session:
            session.add(
                Group(chat_id=-1001, club_id=1, name="GTO / 8190-5287 / Pirate")
            )
            _seed_variant(
                session,
                club_id=1,
                slug="venmo",
                tag="@club-venmo",
                method_id=10,
                variant_id=30,
            )
            session.add(
                GroupPaymentMethodBinding(
                    id=7,
                    telegram_chat_id=-1001,
                    club_id=1,
                    payment_method_slug="venmo",
                    variant_id=30,
                    venmo_handle="@pat-payer",
                    bound_via="manual_dashboard",
                )
            )
            session.add(
                PaymentMethodBindAttempt(
                    id=80,
                    telegram_chat_id=-1001,
                    club_id=1,
                    payment_method_slug="venmo",
                    method_id=10,
                    tier_id=100,
                    variant_id=30,
                    status="pending",
                    amount_cents=1234,
                    expires_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
                )
            )
            session.add(
                GroupDepositDestinationStickiness(
                    telegram_chat_id=-1001,
                    club_id=1,
                    payment_method_slug="venmo",
                    destination_tag="@club-venmo",
                    variant_id=30,
                )
            )
            session.commit()

    def _binding(self):
        with self.factory() as session:
            return session.get(GroupPaymentMethodBinding, 7)

    def test_dry_run_shows_row_and_deletes_nothing(self) -> None:
        out = unbind_payment(7, ctx=None)
        self.assertTrue(out["refused"])
        self.assertFalse(out["applied"])
        self.assertEqual(out["actor"], "tester")
        current = out["current"]
        self.assertEqual(current["id"], 7)
        self.assertEqual(current["telegram_chat_id"], -1001)
        self.assertEqual(current["group_title"], "GTO / 8190-5287 / Pirate")
        self.assertEqual(current["gg_player_id"], "8190-5287")
        self.assertEqual(current["club_name"], "ClubGTO")
        self.assertEqual(current["payment_method_slug"], "venmo")
        self.assertEqual(current["variant_id"], 30)
        self.assertEqual(current["variant_label"], "@club-venmo")
        self.assertEqual(current["bound_via"], "manual_dashboard")
        self.assertEqual(current["venmo_handle"], "@pa***")  # masked
        self.assertEqual(out["proposed"], {"action": "delete", "binding_id": 7})
        self.assertTrue(any("unbound" in note for note in out["notes"]))
        self.assertIsNotNone(self._binding())

    def test_confirm_removes_binding_and_side_rows(self) -> None:
        out = unbind_payment(7, confirm=True, ctx=None)
        self.assertTrue(out["applied"])
        self.assertEqual(out["before"]["id"], 7)
        self.assertIsNone(out["after"])
        self.assertIn("GTO / 8190-5287 / Pirate", out["message"])
        self.assertIsNone(self._binding())
        with self.factory() as session:
            attempt = session.get(PaymentMethodBindAttempt, 80)
            self.assertEqual(attempt.status, "cancelled")
            self.assertEqual(
                session.query(GroupDepositDestinationStickiness).count(), 0
            )

    def test_unknown_binding_is_tool_error(self) -> None:
        with self.assertRaises(ToolError) as cm:
            unbind_payment(999)
        self.assertIn("not found", str(cm.exception))
        with self.assertRaises(ToolError):
            unbind_payment(999, confirm=True)


# ── Payment quick links ──────────────────────────────────────────────────────


class QuickLinkTests(_Case):
    tables = ["clubs", "payment_quick_links"]

    def setUp(self) -> None:
        super().setUp()
        with self.factory() as session:
            session.add(
                PaymentQuickLink(
                    id=3,
                    title="Venmo login",
                    url="https://venmo.com/",
                    method="venmo",
                    club_id=1,
                    sort_order=0,
                )
            )
            session.commit()

    def _rows(self) -> list[PaymentQuickLink]:
        with self.factory() as session:
            return session.query(PaymentQuickLink).order_by(PaymentQuickLink.id).all()

    def test_create_dry_run(self) -> None:
        out = create_payment_quick_link(
            " Stripe ", "https://dashboard.stripe.com", method="Stripe", ctx=None
        )
        self.assertTrue(out["refused"])
        self.assertIsNone(out["current"])
        self.assertEqual(
            out["proposed"],
            {
                "title": "Stripe",
                "url": "https://dashboard.stripe.com",
                "method": "stripe",
                "club_id": None,
                "club_name": None,
            },
        )
        self.assertEqual(len(self._rows()), 1)

    def test_create_confirm_appends(self) -> None:
        out = create_payment_quick_link(
            "Stripe",
            "https://dashboard.stripe.com",
            method="all",
            club="clubgto",
            confirm=True,
            ctx=None,
        )
        self.assertTrue(out["applied"])
        after = out["after"]
        self.assertIsNone(after["method"])
        self.assertEqual(after["club_id"], 1)
        self.assertEqual(after["club_name"], "ClubGTO")
        self.assertEqual(after["sort_order"], 1)
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].title, "Stripe")

    def test_create_validation_errors(self) -> None:
        with self.assertRaises(ToolError) as cm:
            create_payment_quick_link("x", "https://ok.example", method="wire")
        self.assertIn("method must be", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            create_payment_quick_link("x", "ftp://nope.example")
        self.assertIn("http", str(cm.exception))
        with self.assertRaises(ToolError):
            create_payment_quick_link("  ", "https://ok.example")
        with self.assertRaises(ToolError):
            create_payment_quick_link("x", "https://ok.example", club="nope")
        self.assertEqual(len(self._rows()), 1)

    def test_update_dry_run_and_confirm(self) -> None:
        out = update_payment_quick_link(3, title="Venmo", method="all", ctx=None)
        self.assertTrue(out["refused"])
        self.assertEqual(out["current"]["title"], "Venmo login")
        self.assertEqual(out["proposed"], {"title": "Venmo", "method": None})
        self.assertEqual(self._rows()[0].title, "Venmo login")

        out = update_payment_quick_link(
            3, title="Venmo", method="all", club="all", confirm=True, ctx=None
        )
        self.assertTrue(out["applied"])
        self.assertEqual(out["before"]["method"], "venmo")
        self.assertEqual(out["after"]["title"], "Venmo")
        self.assertIsNone(out["after"]["method"])
        self.assertIsNone(out["after"]["club_id"])
        row = self._rows()[0]
        self.assertEqual(row.title, "Venmo")
        self.assertIsNone(row.method)
        self.assertIsNone(row.club_id)

    def test_update_errors(self) -> None:
        with self.assertRaises(ToolError) as cm:
            update_payment_quick_link(3)
        self.assertIn("at least one", str(cm.exception))
        with self.assertRaises(ToolError):
            update_payment_quick_link(3, url="not a url", confirm=True)
        with self.assertRaises(ToolError) as cm:
            update_payment_quick_link(999, title="x")
        self.assertIn("not found", str(cm.exception))
        self.assertEqual(self._rows()[0].url, "https://venmo.com/")

    def test_delete(self) -> None:
        out = delete_payment_quick_link(3, ctx=None)
        self.assertTrue(out["refused"])
        self.assertEqual(out["current"]["title"], "Venmo login")
        self.assertEqual(out["current"]["url"], "https://venmo.com/")
        self.assertEqual(len(self._rows()), 1)

        out = delete_payment_quick_link(3, confirm=True, ctx=None)
        self.assertTrue(out["applied"])
        self.assertEqual(out["before"]["id"], 3)
        self.assertIsNone(out["after"])
        self.assertEqual(self._rows(), [])
        with self.assertRaises(ToolError):
            delete_payment_quick_link(3, confirm=True)


# ── Outbound sends ───────────────────────────────────────────────────────────


class _RoundTableCase(_Case):
    club_name = "Round Table"

    def setUp(self) -> None:
        super().setUp()
        with self.factory() as session:
            _seed_variant(
                session,
                club_id=1,
                slug="venmo",
                tag="@Godfather4444",
                method_id=10,
                variant_id=30,
            )
            session.commit()


class OutboundSendTests(_RoundTableCase):
    tables = ["clubs", *VARIANT_TABLES, "outbound_sends"]

    def setUp(self) -> None:
        super().setUp()
        with self.factory() as session:
            session.add(
                OutboundSend(
                    id=5,
                    method="venmo",
                    tag="@godfather4444",
                    tag_matched=True,
                    method_owner="round-table",
                    recipient="Pat Player",
                    amount_cents=10000,
                    source_external_id="zap-1",
                    paid_at="2026-10-10T12:00:00Z",
                )
            )
            session.commit()

    def _rows(self) -> list[OutboundSend]:
        with self.factory() as session:
            return session.query(OutboundSend).order_by(OutboundSend.id).all()

    def test_record_dry_run(self) -> None:
        out = record_outbound_send(
            "Venmo", "Godfather4444", 125.5, " Pat  Player ", ctx=None
        )
        self.assertTrue(out["refused"])
        self.assertIsNone(out["current"])
        proposed = out["proposed"]
        self.assertEqual(proposed["method"], "venmo")
        self.assertEqual(proposed["tag"], "@go***")  # handle masked by envelope
        self.assertEqual(proposed["amount_cents"], 12550)
        self.assertEqual(proposed["amount_usd"], 125.5)
        self.assertEqual(proposed["method_owner"], "round-table")
        self.assertTrue(proposed["tag_matched"])
        self.assertTrue(proposed["source_external_id"].startswith("mcp-tester-"))
        self.assertIsNone(proposed["paid_at"])
        self.assertTrue(any("balance" in note for note in out["notes"]))
        self.assertEqual(len(self._rows()), 1)

    def test_record_confirm(self) -> None:
        out = record_outbound_send(
            "cashapp",
            "ClubRound",
            40,
            "Pat Player",
            paid_at="2026-10-11T09:00:00Z",
            source_external_id="manual-7",
            confirm=True,
            ctx=None,
        )
        self.assertTrue(out["applied"])
        self.assertIsNone(out["before"])
        after = out["after"]
        self.assertEqual(after["amount_cents"], 4000)
        self.assertFalse(after["tag_matched"])
        self.assertEqual(after["source_external_id"], "manual-7")
        self.assertIn("$40.00", out["message"])
        self.assertTrue(any("does not match" in note for note in out["notes"]))
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].tag, "$clubround")
        self.assertEqual(rows[1].paid_at, "2026-10-11T09:00:00Z")

    def test_record_validation_errors(self) -> None:
        with self.assertRaises(ToolError) as cm:
            record_outbound_send("zelle", "x", 10, "Pat")
        self.assertIn("method must be", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            record_outbound_send("venmo", "x", 0, "Pat")
        self.assertIn("greater than zero", str(cm.exception))
        with self.assertRaises(ToolError):
            record_outbound_send("venmo", "x", 10, "   ")
        with self.assertRaises(ToolError):
            record_outbound_send("venmo", "x", 10, "Pat", method_owner="someone")
        with self.assertRaises(ToolError) as cm:
            record_outbound_send(
                "venmo", "x", 10, "Pat", source_external_id="zap-1", confirm=True
            )
        self.assertIn("already exists", str(cm.exception))
        self.assertEqual(len(self._rows()), 1)

    def test_update_dry_run_and_confirm(self) -> None:
        out = update_outbound_send(5, amount=150, ctx=None)
        self.assertTrue(out["refused"])
        self.assertEqual(out["current"]["amount_cents"], 10000)
        self.assertEqual(out["proposed"]["amount_cents"], 15000)
        self.assertEqual(out["proposed"]["amount_usd"], 150.0)
        self.assertNotIn("recipient", out["proposed"])
        self.assertEqual(self._rows()[0].amount_cents, 10000)

        out = update_outbound_send(5, amount=150, paid_at="", confirm=True, ctx=None)
        self.assertTrue(out["applied"])
        self.assertEqual(out["before"]["amount_cents"], 10000)
        self.assertEqual(out["after"]["amount_cents"], 15000)
        self.assertIsNone(out["after"]["paid_at"])
        row = self._rows()[0]
        self.assertEqual(row.amount_cents, 15000)
        self.assertIsNone(row.paid_at)
        self.assertEqual(row.recipient, "Pat Player")
        self.assertEqual(row.source_external_id, "zap-1")

    def test_update_recomputes_tag_matched(self) -> None:
        out = update_outbound_send(5, tag="@someoneelse", confirm=True)
        self.assertFalse(out["after"]["tag_matched"])
        self.assertFalse(self._rows()[0].tag_matched)

    def test_update_errors(self) -> None:
        with self.assertRaises(ToolError) as cm:
            update_outbound_send(5)
        self.assertIn("at least one", str(cm.exception))
        with self.assertRaises(ToolError):
            update_outbound_send(5, amount=-1, confirm=True)
        with self.assertRaises(ToolError):
            update_outbound_send(5, method="paypal", confirm=True)
        with self.assertRaises(ToolError) as cm:
            update_outbound_send(999, amount=1)
        self.assertIn("not found", str(cm.exception))
        self.assertEqual(self._rows()[0].amount_cents, 10000)

    def test_delete(self) -> None:
        out = delete_outbound_send(5, ctx=None)
        self.assertTrue(out["refused"])
        self.assertEqual(out["current"]["id"], 5)
        self.assertEqual(out["current"]["amount_cents"], 10000)
        self.assertEqual(out["current"]["source_external_id"], "zap-1")
        self.assertEqual(len(self._rows()), 1)

        out = delete_outbound_send(5, confirm=True, ctx=None)
        self.assertTrue(out["applied"])
        self.assertIsNone(out["after"])
        self.assertEqual(self._rows(), [])
        with self.assertRaises(ToolError):
            delete_outbound_send(5, confirm=True)


# ── Account balance settings ─────────────────────────────────────────────────


class AccountBalanceSettingsTests(_RoundTableCase):
    tables = [
        "clubs",
        *VARIANT_TABLES,
        "outbound_sends",
        "venmo_payments",
        "cashapp_payments",
        "paypal_payments",
        "crypto_payments",
        "account_balance_settings",
    ]

    def _settings(self) -> AccountBalanceSettings | None:
        with self.factory() as session:
            return session.query(AccountBalanceSettings).one_or_none()

    def test_dry_run_shows_defaults(self) -> None:
        out = update_account_balance_settings(
            "venmo", "Godfather4444", sending_ceiling=7500, ctx=None
        )
        self.assertTrue(out["refused"])
        current = out["current"]
        self.assertEqual(current["method"], "venmo")
        self.assertEqual(current["offset_cents"], 0)
        self.assertEqual(current["sending_ceiling_cents"], 1_000_000)
        self.assertEqual(current["inflow_max_cents"], 500_000)
        self.assertEqual(out["proposed"], {"sending_ceiling_cents": 750_000})
        self.assertIsNone(self._settings())

    def test_confirm_writes_settings_and_returns_card(self) -> None:
        out = update_account_balance_settings(
            "venmo",
            "@godfather4444",
            offset=-12.34,
            inflow_max=2500,
            confirm=True,
            ctx=None,
        )
        self.assertTrue(out["applied"])
        self.assertEqual(out["before"]["offset_cents"], 0)
        after = out["after"]
        self.assertEqual(after["offset_cents"], -1234)
        self.assertEqual(after["inflow_max_cents"], 250_000)
        self.assertEqual(after["sending_ceiling_cents"], 1_000_000)
        self.assertEqual(after["balance_cents"], -1234)
        self.assertIn("offset_cents", out["message"])
        row = self._settings()
        self.assertEqual(row.tag, "@godfather4444")
        self.assertEqual(row.offset_cents, -1234)
        self.assertEqual(row.inflow_max_cents, 250_000)
        self.assertEqual(row.sending_ceiling_cents, 1_000_000)

    def test_validation_errors(self) -> None:
        with self.assertRaises(ToolError) as cm:
            update_account_balance_settings("zelle", "x", offset=1)
        self.assertIn("paypal", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            update_account_balance_settings("venmo", "Godfather4444")
        self.assertIn("at least one", str(cm.exception))
        with self.assertRaises(ToolError):
            update_account_balance_settings(
                "venmo", "Godfather4444", sending_ceiling=0, confirm=True
            )
        with self.assertRaises(ToolError):
            update_account_balance_settings(
                "venmo", "Godfather4444", inflow_max=-5, confirm=True
            )
        with self.assertRaises(ToolError):
            update_account_balance_settings("venmo", "  ", offset=1)
        self.assertIsNone(self._settings())

    def test_unknown_account_is_tool_error(self) -> None:
        with self.assertRaises(ToolError) as cm:
            update_account_balance_settings("cashapp", "nobody", offset=1)
        self.assertIn("not found", str(cm.exception))
        with self.assertRaises(ToolError):
            update_account_balance_settings("venmo", "janseashells", offset=1)


# ── Deposit method alerts ────────────────────────────────────────────────────


def _fake_week_stats(session, pairs, *, week=None):
    return {pair: WeekStats(volume_cents=0, tx_count=0) for pair in pairs}


class DepositMethodAlertTests(unittest.IsolatedAsyncioTestCase):
    tables = ["clubs", *VARIANT_TABLES, "deposit_method_alerts"]

    def setUp(self) -> None:
        env = patch.dict(os.environ, ENV, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.factory = make_sqlite_session_factory(self.tables)
        mcp_db.session_factory = self.factory
        self.addCleanup(setattr, mcp_db, "session_factory", None)
        self.evaluate = AsyncMock(return_value=1)
        self.release = AsyncMock(return_value=False)
        for target, replacement in (
            (
                "mcp_server.tools.ops.week_stats_map",
                MagicMock(side_effect=_fake_week_stats),
            ),
            ("mcp_server.tools.ops.evaluate_deposit_method_alerts", self.evaluate),
            ("mcp_server.tools.ops.release_disable_latch_if_under_cap", self.release),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        with self.factory() as session:
            session.add(Club(id=1, name="ClubGTO", telegram_user_id=1001))
            session.flush()
            _seed_variant(
                session,
                club_id=1,
                slug="venmo",
                tag="@club-venmo",
                method_id=10,
                variant_id=30,
            )
            session.add(
                DepositMethodAlert(
                    id=4,
                    name="Venmo weekly",
                    method="venmo",
                    variant="@club-venmo",
                    is_active=True,
                    conditions=[
                        {"type": "weekly_volume", "operator": "gte", "threshold": 50000}
                    ],
                    disable_enabled=False,
                    disable_inherits=False,
                    disable_conditions=[],
                    last_fired_week_id="2026-10-12",
                )
            )
            session.commit()

    def _rows(self) -> list[DepositMethodAlert]:
        with self.factory() as session:
            return (
                session.query(DepositMethodAlert).order_by(DepositMethodAlert.id).all()
            )

    async def test_create_dry_run(self) -> None:
        out = await create_deposit_method_alert(
            " Cash App cap ",
            "CashApp",
            "$clubround",
            [{"type": "weekly_volume", "threshold_usd": 500}],
            ctx=None,
        )
        self.assertTrue(out["refused"])
        self.assertIsNone(out["current"])
        proposed = out["proposed"]
        self.assertEqual(proposed["name"], "Cash App cap")
        self.assertEqual(proposed["method"], "cashapp")
        self.assertEqual(proposed["variant"], "$clubround")
        self.assertTrue(proposed["is_active"])
        self.assertEqual(proposed["conditions"][0]["threshold"], 50000)
        self.assertEqual(proposed["conditions"][0]["threshold_usd"], 500.0)
        self.assertEqual(proposed["clubs"], [])
        self.assertTrue(any("Slack" in note for note in out["notes"]))
        self.evaluate.assert_not_awaited()
        self.assertEqual(len(self._rows()), 1)

    async def test_create_confirm_evaluates(self) -> None:
        out = await create_deposit_method_alert(
            "Venmo txs",
            "venmo",
            "@club-venmo",
            [
                {"type": "weekly_transaction_count", "threshold": 25},
                {"type": "weekly_volume", "threshold_usd": "1200.50"},
            ],
            disable_enabled=True,
            disable_inherits=True,
            confirm=True,
            ctx=None,
        )
        self.assertTrue(out["applied"])
        after = out["after"]
        self.assertEqual(after["method"], "venmo")
        self.assertEqual(after["clubs"], [{"id": 1, "name": "ClubGTO"}])
        self.assertTrue(after["disable_enabled"])
        self.assertEqual(after["disable_status"], "on")
        self.assertEqual(after["week_volume_cents"], 0)
        self.evaluate.assert_awaited_once()
        self.assertEqual(
            self.evaluate.await_args.kwargs,
            {"method": "venmo", "variant": "@club-venmo"},
        )
        self.assertTrue(any("1 alert(s)" in note for note in out["notes"]))
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        stored = rows[1].conditions
        self.assertEqual(
            stored,
            [
                {
                    "type": "weekly_transaction_count",
                    "operator": "gte",
                    "threshold": 25,
                },
                {"type": "weekly_volume", "operator": "gte", "threshold": 120050},
            ],
        )

    async def test_create_inactive_skips_evaluate(self) -> None:
        out = await create_deposit_method_alert(
            "Paused",
            "zelle",
            "someone@bank.com",
            [{"type": "weekly_volume", "threshold_usd": 10}],
            is_active=False,
            confirm=True,
        )
        self.assertTrue(out["applied"])
        self.evaluate.assert_not_awaited()
        self.assertTrue(any("not evaluated" in note for note in out["notes"]))

    async def test_create_validation_errors(self) -> None:
        good = [{"type": "weekly_volume", "threshold_usd": 10}]
        with self.assertRaises(ToolError) as cm:
            await create_deposit_method_alert("x", "stripe", "v", good)
        self.assertIn("method must be", str(cm.exception))
        with self.assertRaises(ToolError):
            await create_deposit_method_alert("  ", "venmo", "v", good)
        with self.assertRaises(ToolError):
            await create_deposit_method_alert("x", "venmo", "  ", good)
        with self.assertRaises(ToolError) as cm:
            await create_deposit_method_alert("x", "venmo", "v", [])
        self.assertIn("At least one condition", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            await create_deposit_method_alert(
                "x", "venmo", "v", [{"type": "monthly_volume", "threshold": 1}]
            )
        self.assertIn("Unknown condition type", str(cm.exception))
        with self.assertRaises(ToolError):
            await create_deposit_method_alert(
                "x",
                "venmo",
                "v",
                [{"type": "weekly_volume", "operator": "lt", "threshold_usd": 1}],
            )
        with self.assertRaises(ToolError) as cm:
            await create_deposit_method_alert(
                "x", "venmo", "v", good, disable_enabled=True, confirm=True
            )
        self.assertIn("disable condition", str(cm.exception))
        self.evaluate.assert_not_awaited()
        self.assertEqual(len(self._rows()), 1)

    async def test_update_dry_run(self) -> None:
        out = await update_deposit_method_alert(
            4, conditions=[{"type": "weekly_volume", "threshold_usd": 900}], ctx=None
        )
        self.assertTrue(out["refused"])
        self.assertEqual(out["current"]["id"], 4)
        self.assertEqual(out["current"]["conditions"][0]["threshold_usd"], 500.0)
        self.assertEqual(out["proposed"]["conditions"][0]["threshold"], 90000)
        self.evaluate.assert_not_awaited()
        self.assertEqual(self._rows()[0].conditions[0]["threshold"], 50000)

    async def test_update_conditions_resets_latch_and_evaluates(self) -> None:
        out = await update_deposit_method_alert(
            4,
            name="Venmo weekly (raised)",
            conditions=[{"type": "weekly_volume", "threshold_usd": 900}],
            confirm=True,
            ctx=None,
        )
        self.assertTrue(out["applied"])
        self.assertEqual(out["before"]["last_fired_week_id"], "2026-10-12")
        self.assertIsNone(out["after"]["last_fired_week_id"])
        self.assertEqual(out["after"]["name"], "Venmo weekly (raised)")
        self.assertEqual(out["after"]["conditions"][0]["threshold"], 90000)
        self.evaluate.assert_awaited_once()
        self.release.assert_not_awaited()
        row = self._rows()[0]
        self.assertEqual(row.conditions[0]["threshold"], 90000)
        self.assertIsNone(row.last_fired_week_id)

    async def test_update_name_only_does_not_evaluate(self) -> None:
        out = await update_deposit_method_alert(4, name="Renamed", confirm=True)
        self.assertEqual(out["after"]["name"], "Renamed")
        self.assertEqual(out["after"]["last_fired_week_id"], "2026-10-12")
        self.evaluate.assert_not_awaited()
        self.assertTrue(any("not re-evaluated" in note for note in out["notes"]))

    async def test_update_disable_limits_release_latch(self) -> None:
        self.release.return_value = True
        out = await update_deposit_method_alert(
            4,
            disable_enabled=True,
            disable_conditions=[{"type": "weekly_transaction_count", "threshold": 3}],
            confirm=True,
        )
        self.assertTrue(out["after"]["disable_enabled"])
        self.assertEqual(out["after"]["disable_status"], "on")
        self.release.assert_awaited_once()
        self.evaluate.assert_awaited_once()
        self.assertTrue(any("released" in note for note in out["notes"]))

    async def test_update_errors(self) -> None:
        with self.assertRaises(ToolError) as cm:
            await update_deposit_method_alert(4)
        self.assertIn("at least one", str(cm.exception))
        with self.assertRaises(ToolError):
            await update_deposit_method_alert(4, method="stripe")
        with self.assertRaises(ToolError):
            await update_deposit_method_alert(4, name="   ")
        with self.assertRaises(ToolError):
            await update_deposit_method_alert(
                4, conditions=[{"type": "weekly_volume", "threshold_usd": 0}]
            )
        with self.assertRaises(ToolError) as cm:
            await update_deposit_method_alert(4, disable_enabled=True, confirm=True)
        self.assertIn("disable condition", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            await update_deposit_method_alert(999, name="x")
        self.assertIn("not found", str(cm.exception))
        self.evaluate.assert_not_awaited()
        row = self._rows()[0]
        self.assertEqual(row.name, "Venmo weekly")
        self.assertFalse(row.disable_enabled)

    def test_delete(self) -> None:
        out = delete_deposit_method_alert(4, ctx=None)
        self.assertTrue(out["refused"])
        self.assertEqual(out["current"]["id"], 4)
        self.assertEqual(out["current"]["name"], "Venmo weekly")
        self.assertEqual(out["current"]["variant"], "@club-venmo")
        self.assertEqual(out["proposed"], {"action": "delete", "alert_id": 4})
        self.assertEqual(len(self._rows()), 1)

        out = delete_deposit_method_alert(4, confirm=True, ctx=None)
        self.assertTrue(out["applied"])
        self.assertEqual(out["before"]["id"], 4)
        self.assertIsNone(out["after"])
        self.assertEqual(self._rows(), [])
        with self.assertRaises(ToolError):
            delete_deposit_method_alert(4, confirm=True)


if __name__ == "__main__":
    unittest.main()
