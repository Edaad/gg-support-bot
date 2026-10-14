"""MCP write tools for bonuses, Pool Pay manual deposit requests and issue reports."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import JSON

import mcp_server.db as mcp_db
from db.models import (
    BonusRecord,
    BonusType,
    Club,
    ClubPaymentMethod,
    ClubPaymentMethodClub,
    Group,
    IssueReport,
    ManualDepositRequest,
    PlayerDetails,
)
from mcp_server.tools.bonuses import (
    create_bonus_record,
    create_bonus_type,
    delete_bonus_record,
    delete_bonus_type,
    update_bonus_record,
    update_bonus_type,
)
from mcp_server.tools.ops import create_issue_report
from mcp_server.tools.pool_pay import (
    create_manual_deposit_request,
    delete_manual_deposit_request,
    update_manual_deposit_request,
)
from tests.support.sqlite_db import get_db_for, make_sqlite_session_factory

ENV = {"MCP_API_TOKENS": "dev:" + "x" * 32, "MCP_ACTOR": "tester"}
GET_DB_MODULES = (
    "bot.services.bonus_records",
    "bot.services.bonus_player_resolve",
    "bot.services.player_details",
    "bot.services.manual_deposit_requests",
    "bot.services.club",
)
SLACK_POOL_PAY = "bot.services.escalation_notification.notify_pool_pay_deposit_slack"
SLACK_ISSUE = "bot.services.slack_ops_notify.notify_slack_issue_report"


def _install(case: unittest.TestCase, tables: list[str]):
    env = patch.dict(os.environ, ENV, clear=False)
    env.start()
    case.addCleanup(env.stop)
    # The SQLite helper swaps ARRAY for JSON in the DDL only; the ORM still binds
    # through the mapped column, so give those the same JSON type while the test
    # runs (restored afterwards).
    for column in (
        PlayerDetails.__table__.c.chat_ids,
        IssueReport.__table__.c.tags,
        IssueReport.__table__.c.notify_tags,
    ):
        patcher = patch.object(column, "type", JSON())
        patcher.start()
        case.addCleanup(patcher.stop)
    factory = make_sqlite_session_factory(tables)
    mcp_db.session_factory = factory
    case.addCleanup(setattr, mcp_db, "session_factory", None)
    for module in GET_DB_MODULES:
        patcher = patch(f"{module}.get_db", get_db_for(factory))
        patcher.start()
        case.addCleanup(patcher.stop)
    with factory() as session:
        session.add(Club(id=1, name="ClubGTO", telegram_user_id=1001))
        session.add(Club(id=2, name="Round Table", telegram_user_id=1002))
        session.commit()
    return factory


# ── Bonuses ───────────────────────────────────────────────────────────────────


class _BonusCase(unittest.TestCase):
    tables = ["clubs", "groups", "player_details", "bonus_types", "bonus_records"]

    def setUp(self) -> None:
        self.factory = _install(self, self.tables)
        with self.factory() as session:
            session.add(BonusType(id=2, name="Referral", is_active=True, sort_order=1))
            session.add(BonusType(id=3, name="Retired", is_active=False, sort_order=2))
            session.add(
                Group(chat_id=-1001, club_id=1, name="GTO / 8190-5287 / ThePirate343")
            )
            session.add(
                PlayerDetails(
                    id=77, chat_ids=[-1001], gg_player_id="8190-5287", club_id=1
                )
            )
            session.commit()

    def _records(self) -> list[BonusRecord]:
        with self.factory() as session:
            return session.query(BonusRecord).order_by(BonusRecord.id).all()


class CreateBonusRecordTests(_BonusCase):
    def test_dry_run_resolves_player_and_creates_nothing(self) -> None:
        out = create_bonus_record(
            "clubgto", 50, group_title="GTO / 8190-5287 / ThePirate343", bonus_type_id=2
        )
        self.assertTrue(out["refused"])
        self.assertFalse(out["applied"])
        self.assertEqual(out["actor"], "tester")
        self.assertIsNone(out["current"])
        proposed = out["proposed"]
        self.assertEqual(proposed["club_id"], 1)
        self.assertEqual(proposed["club_name"], "ClubGTO")
        self.assertEqual(proposed["gg_player_id"], "8190-5287")
        self.assertEqual(proposed["amount"], 50.0)
        self.assertEqual(proposed["bonus_type_name"], "Referral")
        self.assertIsNone(proposed["issued_at"])
        self.assertTrue(any("Telegram" in n for n in out["notes"]))
        self.assertEqual(self._records(), [])

    def test_confirm_creates_resolved_record(self) -> None:
        out = create_bonus_record(
            "1",
            50,
            group_title="GTO / 8190-5287 / ThePirate343",
            bonus_type_id=2,
            issued_at="2026-07-01T12:00:00",
            confirm=True,
        )
        self.assertTrue(out["applied"])
        self.assertIsNone(out["before"])
        after = out["after"]
        self.assertEqual(after["gg_player_id"], "8190-5287")
        self.assertEqual(after["player_details_id"], 77)
        self.assertEqual(after["bonus_type_name"], "Referral")
        self.assertTrue(after["player_resolved"])
        self.assertEqual(after["club_name"], "ClubGTO")
        # player_username is a PII key and masked in the envelope
        self.assertEqual(after["player_username"], "@GT***")
        rows = self._records()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].amount, Decimal("50"))
        self.assertEqual(rows[0].bonus_type_id, 2)
        self.assertEqual(rows[0].group_title, "GTO / 8190-5287 / ThePirate343")
        self.assertEqual(
            rows[0].issued_at.replace(tzinfo=None), datetime(2026, 7, 1, 12)
        )

    def test_gg_player_id_finds_linked_group(self) -> None:
        out = create_bonus_record(
            "clubgto", 25, gg_player_id="8190-5287", custom_description="Welcome"
        )
        self.assertEqual(
            out["proposed"]["group_title"], "GTO / 8190-5287 / ThePirate343"
        )
        self.assertIsNone(out["proposed"]["bonus_type_id"])
        self.assertEqual(out["proposed"]["custom_description"], "Welcome")
        self.assertTrue(any("linked group chat -1001" in n for n in out["notes"]))

    def test_gg_player_id_without_group_uses_synthetic_title(self) -> None:
        out = create_bonus_record(
            "round-table", 25, gg_player_id="1111-2222", bonus_type_id=2, confirm=True
        )
        self.assertEqual(out["after"]["group_title"], "RT / 1111-2222")
        self.assertEqual(out["after"]["gg_player_id"], "1111-2222")
        self.assertEqual(out["after"]["club_id"], 2)
        self.assertTrue(any("synthetic" in n for n in out["notes"]))

    def test_validation_errors(self) -> None:
        title = "GTO / 8190-5287 / ThePirate343"
        with self.assertRaises(ToolError):
            create_bonus_record("clubgto", 0, group_title=title, bonus_type_id=2)
        with self.assertRaises(ToolError) as cm:
            create_bonus_record("clubgto", 10, group_title=title)
        self.assertIn("custom_description", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            create_bonus_record("clubgto", 10, group_title=title, bonus_type_id=3)
        self.assertIn("not active", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            create_bonus_record("clubgto", 10, group_title=title, bonus_type_id=99)
        self.assertIn("not found", str(cm.exception))
        with self.assertRaises(ToolError):
            create_bonus_record("nope", 10, group_title=title, bonus_type_id=2)
        with self.assertRaises(ToolError) as cm:
            create_bonus_record("clubgto", 10, bonus_type_id=2)
        self.assertIn("group_title or gg_player_id", str(cm.exception))
        with self.assertRaises(ToolError):
            create_bonus_record("clubgto", 10, gg_player_id="pirate", bonus_type_id=2)
        with self.assertRaises(ToolError) as cm:
            create_bonus_record(
                "clubgto",
                10,
                group_title=title,
                gg_player_id="0000-0001",
                bonus_type_id=2,
            )
        self.assertIn("different players", str(cm.exception))
        with self.assertRaises(ToolError):
            create_bonus_record(
                "clubgto",
                10,
                group_title=title,
                bonus_type_id=2,
                issued_at="07/01/2026",
            )
        self.assertEqual(self._records(), [])


class UpdateDeleteBonusRecordTests(_BonusCase):
    def setUp(self) -> None:
        super().setUp()
        with self.factory() as session:
            session.add(
                BonusRecord(
                    id=5,
                    player_username="GTO / 8190-5287 / ThePirate343",
                    amount=Decimal("50.00"),
                    bonus_type_id=2,
                    club_id=1,
                    gg_player_id="8190-5287",
                    group_title="GTO / 8190-5287 / ThePirate343",
                    issued_at=datetime(2026, 7, 1, 12),
                )
            )
            session.commit()

    def _row(self) -> BonusRecord | None:
        with self.factory() as session:
            return session.get(BonusRecord, 5)

    def test_update_dry_run(self) -> None:
        out = update_bonus_record(5, amount=75)
        self.assertTrue(out["refused"])
        self.assertEqual(out["current"]["id"], 5)
        self.assertEqual(out["current"]["amount"], 50.0)
        self.assertEqual(out["proposed"], {"amount": 75.0})
        self.assertEqual(self._row().amount, Decimal("50.00"))

    def test_update_confirm_amount_and_other(self) -> None:
        out = update_bonus_record(
            5, amount=75, clear_bonus_type=True, custom_description="Comp", confirm=True
        )
        self.assertTrue(out["applied"])
        self.assertEqual(out["before"]["bonus_type_name"], "Referral")
        self.assertIsNone(out["after"]["bonus_type_id"])
        self.assertEqual(out["after"]["custom_description"], "Comp")
        self.assertEqual(out["after"]["amount"], 75.0)
        row = self._row()
        self.assertIsNone(row.bonus_type_id)
        self.assertEqual(row.amount, Decimal("75"))

    def test_update_club_and_issued_at(self) -> None:
        out = update_bonus_record(
            5, club="round-table", issued_at="2026-08-01T00:00:00Z", confirm=True
        )
        self.assertEqual(out["after"]["club_id"], 2)
        self.assertEqual(out["after"]["club_name"], "Round Table")
        # the title still says GTO, so the player no longer resolves for club 2
        self.assertFalse(out["after"]["player_resolved"])
        self.assertEqual(
            self._row().issued_at.replace(tzinfo=None), datetime(2026, 8, 1)
        )

    def test_update_validation(self) -> None:
        with self.assertRaises(ToolError) as cm:
            update_bonus_record(5)
        self.assertIn("at least one", str(cm.exception))
        with self.assertRaises(ToolError):
            update_bonus_record(5, amount=0)
        with self.assertRaises(ToolError):
            update_bonus_record(5, bonus_type_id=2, clear_bonus_type=True)
        with self.assertRaises(ToolError) as cm:
            update_bonus_record(5, clear_bonus_type=True)
        self.assertIn("custom_description", str(cm.exception))
        with self.assertRaises(ToolError):
            update_bonus_record(5, bonus_type_id=3)
        with self.assertRaises(ToolError):
            update_bonus_record(5, group_title="   ")
        with self.assertRaises(ToolError) as cm:
            update_bonus_record(999, amount=10)
        self.assertIn("not found", str(cm.exception))
        self.assertEqual(self._row().amount, Decimal("50.00"))

    def test_delete_dry_run_shows_row(self) -> None:
        out = delete_bonus_record(5)
        self.assertTrue(out["refused"])
        self.assertEqual(out["current"]["id"], 5)
        self.assertEqual(out["current"]["amount"], 50.0)
        self.assertEqual(out["proposed"], {"action": "delete"})
        self.assertIsNotNone(self._row())

    def test_delete_confirm(self) -> None:
        out = delete_bonus_record(5, confirm=True)
        self.assertTrue(out["applied"])
        self.assertEqual(out["before"]["id"], 5)
        self.assertIsNone(out["after"])
        self.assertIsNone(self._row())
        with self.assertRaises(ToolError):
            delete_bonus_record(5, confirm=True)


class BonusTypeTests(_BonusCase):
    def _types(self) -> dict[int, BonusType]:
        with self.factory() as session:
            return {int(t.id): t for t in session.query(BonusType).all()}

    def test_create_dry_run_and_confirm(self) -> None:
        out = create_bonus_type("  Rakeback ", sort_order=5)
        self.assertTrue(out["refused"])
        self.assertEqual(
            out["proposed"], {"name": "Rakeback", "sort_order": 5, "is_active": True}
        )
        self.assertEqual(set(self._types()), {2, 3})

        out = create_bonus_type("Rakeback", sort_order=5, is_active=False, confirm=True)
        self.assertTrue(out["applied"])
        self.assertEqual(out["after"]["name"], "Rakeback")
        self.assertFalse(out["after"]["is_active"])
        self.assertEqual(out["after"]["sort_order"], 5)
        created = self._types()[out["after"]["id"]]
        self.assertEqual(created.name, "Rakeback")

    def test_create_duplicate_and_blank(self) -> None:
        with self.assertRaises(ToolError) as cm:
            create_bonus_type("referral", confirm=True)
        self.assertIn("conflict", str(cm.exception))
        with self.assertRaises(ToolError):
            create_bonus_type("   ")
        self.assertEqual(set(self._types()), {2, 3})

    def test_update(self) -> None:
        out = update_bonus_type(2, is_active=False)
        self.assertTrue(out["refused"])
        self.assertEqual(out["current"]["name"], "Referral")
        self.assertEqual(out["proposed"], {"is_active": False})
        self.assertTrue(self._types()[2].is_active)

        out = update_bonus_type(2, name="Referral bonus", is_active=False, confirm=True)
        self.assertTrue(out["applied"])
        self.assertEqual(out["after"]["name"], "Referral bonus")
        self.assertFalse(out["after"]["is_active"])
        row = self._types()[2]
        self.assertEqual(row.name, "Referral bonus")
        self.assertFalse(row.is_active)

    def test_update_validation(self) -> None:
        with self.assertRaises(ToolError):
            update_bonus_type(2)
        with self.assertRaises(ToolError):
            update_bonus_type(2, name=" ")
        with self.assertRaises(ToolError) as cm:
            update_bonus_type(2, name="RETIRED")
        self.assertIn("conflict", str(cm.exception))
        with self.assertRaises(ToolError):
            update_bonus_type(99, name="x")
        self.assertEqual(self._types()[2].name, "Referral")

    def test_delete_reports_references_and_nulls_them(self) -> None:
        with self.factory() as session:
            session.add(
                BonusRecord(
                    id=9,
                    player_username="GTO / 8190-5287 / ThePirate343",
                    amount=Decimal("10"),
                    bonus_type_id=2,
                    club_id=1,
                    issued_at=datetime(2026, 7, 1),
                )
            )
            session.commit()
        out = delete_bonus_type(2)
        self.assertTrue(out["refused"])
        self.assertEqual(out["current"]["name"], "Referral")
        self.assertEqual(
            out["proposed"], {"action": "delete", "records_referencing": 1}
        )
        self.assertIn(2, self._types())

        out = delete_bonus_type(2, confirm=True)
        self.assertTrue(out["applied"])
        self.assertIsNone(out["after"])
        self.assertNotIn(2, self._types())
        with self.factory() as session:
            self.assertIsNone(session.get(BonusRecord, 9).bonus_type_id)
        with self.assertRaises(ToolError):
            delete_bonus_type(2, confirm=True)


# ── Pool Pay manual deposit requests ──────────────────────────────────────────

POOL_TABLES = [
    "clubs",
    "groups",
    "club_payment_methods",
    "club_payment_method_clubs",
    "manual_deposit_requests",
]


def _seed_pool_pay(factory) -> None:
    with factory() as session:
        session.add(
            ClubPaymentMethod(
                id=10,
                club_id=1,
                direction="deposit",
                name="Zelle",
                slug="zelle-union-tmt",
                tracks_manual_requests=True,
                union_type="zelle",
                pool_pay_type="union_method",
                deposit_union="tmt",
                method_tag="pool@bank.com",
                deposit_limit=Decimal("10000"),
            )
        )
        session.add(
            ClubPaymentMethod(
                id=11,
                club_id=1,
                direction="deposit",
                name="Venmo",
                slug="venmo",
                tracks_manual_requests=False,
            )
        )
        session.flush()
        session.add(ClubPaymentMethodClub(method_id=10, club_id=1))
        session.add(
            Group(chat_id=-1001, club_id=1, name="GTO / 8190-5287 / ThePirate343")
        )
        session.add(
            Group(chat_id=-1002, club_id=1, name="GTO / 1111-2222 / Pirate Two")
        )
        session.add(Group(chat_id=-2001, club_id=2, name="RT / 3333-4444 / Elsewhere"))
        session.commit()


class CreateManualDepositRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.factory = _install(self, POOL_TABLES)
        _seed_pool_pay(self.factory)
        self.slack = AsyncMock(return_value=True)
        patcher = patch(SLACK_POOL_PAY, self.slack)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _rows(self) -> list[ManualDepositRequest]:
        with self.factory() as session:
            return session.query(ManualDepositRequest).all()

    async def test_dry_run_resolves_group_and_creates_nothing(self) -> None:
        out = await create_manual_deposit_request(10, 300, group_title="ThePirate343")
        self.assertTrue(out["refused"])
        self.assertEqual(out["actor"], "tester")
        proposed = out["proposed"]
        self.assertEqual(proposed["telegram_chat_id"], -1001)
        self.assertEqual(proposed["group_title"], "GTO / 8190-5287 / ThePirate343")
        self.assertEqual(proposed["club_id"], 1)
        self.assertEqual(proposed["club_name"], "ClubGTO")
        self.assertEqual(proposed["method_name"], "Zelle")
        self.assertEqual(proposed["variant_name"], "pool@bank.com")
        self.assertEqual(proposed["amount"], 300.0)
        self.assertEqual(proposed["source"], "dashboard")
        self.assertTrue(proposed["slack_notification"])
        self.assertIn("amounts are dollars", out["notes"])
        self.assertEqual(self._rows(), [])
        self.slack.assert_not_awaited()

    async def test_confirm_creates_row_and_posts_slack(self) -> None:
        out = await create_manual_deposit_request(
            10,
            300,
            group_title="GTO / 8190-5287 / ThePirate343",
            created_at="2026-07-01T12:00:00",
            confirm=True,
        )
        self.assertTrue(out["applied"])
        self.assertIsNone(out["before"])
        after = out["after"]
        self.assertEqual(after["telegram_chat_id"], -1001)
        self.assertEqual(after["amount"], 300.0)
        self.assertEqual(after["source"], "dashboard")
        self.assertEqual(after["variant_name"], "pool@bank.com")
        self.assertEqual(after["club"]["name"], "ClubGTO")
        self.assertFalse(after["trade_record_checked"])
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].method_id, 10)
        self.assertEqual(rows[0].amount, Decimal("300"))
        self.assertEqual(
            rows[0].created_at.replace(tzinfo=None), datetime(2026, 7, 1, 12)
        )
        self.slack.assert_awaited_once()
        kwargs = self.slack.await_args.kwargs
        self.assertEqual(kwargs["variant"], "first")
        self.assertEqual(kwargs["chat_id"], -1001)
        self.assertEqual(kwargs["method_display_name"], "Zelle")
        self.assertEqual(kwargs["deposit_union"], "tmt")
        self.assertTrue(any("posted" in n for n in out["notes"]))

    async def test_confirm_by_chat_id_without_slack(self) -> None:
        out = await create_manual_deposit_request(
            10,
            50,
            telegram_chat_id=-1002,
            trade_record_checked=True,
            notify_slack=False,
            confirm=True,
        )
        self.assertTrue(out["applied"])
        self.assertEqual(out["after"]["group_title"], "GTO / 1111-2222 / Pirate Two")
        self.assertTrue(out["after"]["trade_record_checked"])
        self.slack.assert_not_awaited()
        self.assertTrue(any("no Slack" in n for n in out["notes"]))

    async def test_slack_failure_is_ignored(self) -> None:
        self.slack.side_effect = RuntimeError("slack down")
        out = await create_manual_deposit_request(
            10, 50, telegram_chat_id=-1001, confirm=True
        )
        self.assertTrue(out["applied"])
        self.assertEqual(len(self._rows()), 1)
        self.assertTrue(any("not posted" in n for n in out["notes"]))

    async def test_validation_errors(self) -> None:
        with self.assertRaises(ToolError):
            await create_manual_deposit_request(10, 0, telegram_chat_id=-1001)
        with self.assertRaises(ToolError) as cm:
            await create_manual_deposit_request(99, 10, telegram_chat_id=-1001)
        self.assertIn("not found", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            await create_manual_deposit_request(11, 10, telegram_chat_id=-1001)
        self.assertIn("not a Pool Pay", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            await create_manual_deposit_request(10, 10)
        self.assertIn("telegram_chat_id or group_title", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            await create_manual_deposit_request(10, 10, telegram_chat_id=-2001)
        self.assertIn("not found", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            await create_manual_deposit_request(10, 10, group_title="GTO /")
        self.assertIn("several groups", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            await create_manual_deposit_request(10, 10, group_title="Nobody")
        self.assertIn("not found", str(cm.exception))
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with self.assertRaises(ToolError) as cm:
            await create_manual_deposit_request(
                10, 10, telegram_chat_id=-1001, created_at=future
            )
        self.assertIn("future", str(cm.exception))
        self.assertEqual(self._rows(), [])
        self.slack.assert_not_awaited()


class UpdateDeleteManualDepositRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = _install(self, POOL_TABLES)
        _seed_pool_pay(self.factory)
        with self.factory() as session:
            session.add(
                ManualDepositRequest(
                    id=7,
                    club_id=1,
                    method_id=10,
                    method_name="Zelle",
                    method_slug="zelle-union-tmt",
                    variant_name="pool@bank.com",
                    group_title="GTO / 8190-5287 / ThePirate343",
                    amount=Decimal("300.00"),
                    telegram_chat_id=-1001,
                    trade_record_checked=False,
                    created_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
                )
            )
            session.commit()

    def _row(self) -> ManualDepositRequest | None:
        with self.factory() as session:
            return session.get(ManualDepositRequest, 7)

    def test_existing_params_still_work(self) -> None:
        out = update_manual_deposit_request(7, trade_record_checked=True, amount=350)
        self.assertTrue(out["refused"])
        self.assertEqual(
            out["proposed"], {"trade_record_checked": True, "amount": 350.0}
        )
        out = update_manual_deposit_request(
            7, trade_record_checked=True, amount=350, confirm=True
        )
        self.assertTrue(out["after"]["trade_record_checked"])
        self.assertEqual(out["after"]["amount"], 350.0)
        self.assertEqual(self._row().amount, Decimal("350.00"))

    def test_move_group_dry_run_and_confirm(self) -> None:
        out = update_manual_deposit_request(7, telegram_chat_id=-1002)
        self.assertTrue(out["refused"])
        self.assertEqual(out["proposed"]["telegram_chat_id"], -1002)
        self.assertEqual(out["proposed"]["group_title"], "GTO / 1111-2222 / Pirate Two")
        self.assertEqual(out["proposed"]["club_name"], "ClubGTO")
        self.assertEqual(self._row().telegram_chat_id, -1001)

        out = update_manual_deposit_request(7, telegram_chat_id=-1002, confirm=True)
        self.assertTrue(out["applied"])
        self.assertEqual(out["after"]["telegram_chat_id"], -1002)
        self.assertEqual(out["after"]["group_title"], "GTO / 1111-2222 / Pirate Two")
        self.assertIn("telegram_chat_id", out["message"])
        self.assertEqual(self._row().group_title, "GTO / 1111-2222 / Pirate Two")

    def test_created_at(self) -> None:
        out = update_manual_deposit_request(7, created_at="2026-06-15T09:30:00Z")
        self.assertEqual(out["proposed"], {"created_at": "2026-06-15T09:30:00+00:00"})
        out = update_manual_deposit_request(
            7, created_at="2026-06-15T09:30:00", confirm=True
        )
        self.assertTrue(out["applied"])
        self.assertEqual(
            self._row().created_at.replace(tzinfo=None), datetime(2026, 6, 15, 9, 30)
        )

    def test_new_param_validation(self) -> None:
        with self.assertRaises(ToolError) as cm:
            update_manual_deposit_request(7)
        self.assertIn("trade_record_checked", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            update_manual_deposit_request(7, telegram_chat_id=-2001)
        self.assertIn("not found", str(cm.exception))
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        with self.assertRaises(ToolError) as cm:
            update_manual_deposit_request(7, created_at=future, confirm=True)
        self.assertIn("future", str(cm.exception))
        with self.assertRaises(ToolError):
            update_manual_deposit_request(7, created_at="yesterday")
        with self.assertRaises(ToolError):
            update_manual_deposit_request(999, telegram_chat_id=-1002)
        row = self._row()
        self.assertEqual(row.telegram_chat_id, -1001)
        self.assertEqual(row.created_at.replace(tzinfo=None), datetime(2026, 7, 1, 12))

    def test_delete_dry_run_shows_row(self) -> None:
        out = delete_manual_deposit_request(7)
        self.assertTrue(out["refused"])
        self.assertEqual(out["current"]["id"], 7)
        self.assertEqual(out["current"]["amount"], 300.0)
        self.assertEqual(
            out["current"]["group_title"], "GTO / 8190-5287 / ThePirate343"
        )
        self.assertEqual(out["proposed"], {"action": "delete"})
        self.assertIsNotNone(self._row())

    def test_delete_confirm_and_not_found(self) -> None:
        out = delete_manual_deposit_request(7, confirm=True)
        self.assertTrue(out["applied"])
        self.assertEqual(out["before"]["id"], 7)
        self.assertIsNone(out["after"])
        self.assertIsNone(self._row())
        with self.assertRaises(ToolError) as cm:
            delete_manual_deposit_request(7)
        self.assertIn("not found", str(cm.exception))


# ── Issue reports ─────────────────────────────────────────────────────────────


class CreateIssueReportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.factory = _install(
            self, ["clubs", "groups", "issue_reports", "issue_report_attachments"]
        )
        with self.factory() as session:
            session.add(
                Group(chat_id=-1001, club_id=1, name="GTO / 8190-5287 / ThePirate343")
            )
            session.commit()
        self.slack = AsyncMock(return_value=(True, "1700000000.000100", []))
        patcher = patch(SLACK_ISSUE, self.slack)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _rows(self) -> list[IssueReport]:
        with self.factory() as session:
            return session.query(IssueReport).all()

    async def test_dry_run_creates_nothing(self) -> None:
        out = await create_issue_report(
            "Deposit missing",
            "Player paid $200, no chips",
            category="deposit",
            club="GTO",
        )
        self.assertTrue(out["refused"])
        self.assertEqual(out["actor"], "tester")
        proposed = out["proposed"]
        self.assertEqual(proposed["category"], "deposit")
        self.assertEqual(proposed["notify_tags"], ["head_admin"])
        self.assertEqual(proposed["tags"], [])
        self.assertEqual(proposed["club_id"], 1)
        self.assertEqual(proposed["club_name"], "ClubGTO")
        self.assertEqual(proposed["reporter_source"], "mcp")
        self.assertTrue(proposed["slack_notification"])
        self.assertTrue(any("attachments" in n for n in out["notes"]))
        self.assertEqual(self._rows(), [])
        self.slack.assert_not_awaited()

    async def test_confirm_files_report_and_posts_slack(self) -> None:
        out = await create_issue_report(
            "Bot stuck",
            "  /deposit never answers ",
            tags=["bot_issue"],
            telegram_chat_id=-1001,
            reporter_name="Bob",
            confirm=True,
        )
        self.assertTrue(out["applied"])
        self.assertIsNone(out["before"])
        after = out["after"]
        self.assertEqual(after["status"], "open")
        self.assertEqual(after["category"], "bot_issue")
        self.assertEqual(after["notify_tags"], ["engineer"])
        self.assertEqual(after["club_id"], 1)
        self.assertEqual(after["club_name"], "ClubGTO")
        self.assertEqual(after["group_title"], "GTO / 8190-5287 / ThePirate343")
        self.assertEqual(after["telegram_chat_id"], -1001)
        self.assertEqual(after["slack_message_ts"], "1700000000.000100")
        self.assertEqual(after["reporter_name"], "B***")
        self.assertEqual(after["description"], "/deposit never answers")
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].reporter_name, "Bob")
        self.assertEqual(rows[0].reporter_source, "mcp")
        self.assertEqual(list(rows[0].tags), ["bot_issue"])
        self.slack.assert_awaited_once()
        args, kwargs = self.slack.await_args
        self.assertIn("Bot stuck", args[0])
        self.assertIn("ThePirate343", args[0])
        self.assertIn("ClubGTO", args[0])
        self.assertEqual(kwargs["tags"], ["engineer"])
        self.assertEqual(kwargs["file_bytes"], [])

    async def test_confirm_without_slack(self) -> None:
        out = await create_issue_report(
            "Quiet", "no slack please", notify_slack=False, confirm=True
        )
        self.assertTrue(out["applied"])
        self.assertIsNone(out["after"]["slack_message_ts"])
        self.assertIsNone(out["after"]["category"])
        self.assertEqual(out["after"]["notify_tags"], [])
        self.slack.assert_not_awaited()
        self.assertEqual(len(self._rows()), 1)

    async def test_validation_errors(self) -> None:
        with self.assertRaises(ToolError):
            await create_issue_report("  ", "x")
        with self.assertRaises(ToolError):
            await create_issue_report("x", "  ")
        with self.assertRaises(ToolError) as cm:
            await create_issue_report("x", "y", category="payroll")
        self.assertIn("Invalid category", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            await create_issue_report("x", "y", tags=["urgent"])
        self.assertIn("Invalid tag", str(cm.exception))
        with self.assertRaises(ToolError):
            await create_issue_report("x", "y", club="nope")
        with self.assertRaises(ToolError) as cm:
            await create_issue_report("x", "y", telegram_chat_id=-9999)
        self.assertIn("not found", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            await create_issue_report(
                "x", "y", telegram_chat_id=-1001, club="round-table"
            )
        self.assertIn("belongs to club", str(cm.exception))
        self.assertEqual(self._rows(), [])
        self.slack.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
