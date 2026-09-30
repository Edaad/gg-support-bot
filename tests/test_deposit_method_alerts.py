"""Deposit method weekly alerts: conditions, week bounds, fire-once, API."""

from __future__ import annotations

import os
import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON, create_engine
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.auth import ROLE_ADMIN, create_token
from api.routes.deposit_method_alerts import router
from bot.handlers import deposit as dep
from bot.services import club_payment_v2
from bot.services.deposit_method_alerts import (
    CONDITION_WEEKLY_TX_COUNT,
    CONDITION_WEEKLY_VOLUME,
    WeekStats,
    conditions_equal,
    conditions_from_api,
    conditions_met,
    destination_is_disabled,
    destination_match_key,
    disable_status,
    disabled_destination_keys,
    eastern_week_bounds_utc,
    evaluate_deposit_method_alerts,
    is_destination_disabled,
    maybe_evaluate_after_ingest,
)
from db.connection import get_db_dependency
from db.models import (
    Base,
    Club,
    ClubPaymentMethod,
    ClubPaymentTier,
    ClubPaymentTierVariant,
    DepositMethodAlert,
    VenmoPayment,
)

EASTERN = ZoneInfo("America/New_York")


def _sqlite_json_for_alerts() -> None:
    # SQLite cannot compile PostgreSQL JSONB; use JSON for in-memory tests only.
    DepositMethodAlert.__table__.c.conditions.type = JSON()
    DepositMethodAlert.__table__.c.disable_conditions.type = JSON()


def _make_app(session_factory):
    app = FastAPI()
    app.include_router(router)

    def override_db():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db_dependency] = override_db
    return app


class EasternWeekBoundsTests(unittest.TestCase):
    def test_wednesday_edt(self):
        # 2026-09-16 15:00 UTC = Wed 11:00 EDT
        now = datetime(2026, 9, 16, 15, 0, tzinfo=ZoneInfo("UTC"))
        week = eastern_week_bounds_utc(now)
        self.assertEqual(week.week_id, "2026-09-14")
        monday_et = week.start_utc.astimezone(EASTERN)
        self.assertEqual(monday_et.weekday(), 0)
        self.assertEqual(monday_et.hour, 0)
        self.assertEqual(week.end_utc, now)

    def test_sunday_still_current_week(self):
        # 2026-09-20 18:00 UTC = Sun 14:00 EDT
        now = datetime(2026, 9, 20, 18, 0, tzinfo=ZoneInfo("UTC"))
        week = eastern_week_bounds_utc(now)
        self.assertEqual(week.week_id, "2026-09-14")

    def test_monday_starts_new_week_est(self):
        # 2026-01-12 05:00 UTC = Mon 00:00 EST
        now = datetime(2026, 1, 12, 5, 0, tzinfo=ZoneInfo("UTC"))
        week = eastern_week_bounds_utc(now)
        self.assertEqual(week.week_id, "2026-01-12")


class ConditionsRegistryTests(unittest.TestCase):
    def test_volume_or_count_gte(self):
        conditions = conditions_from_api(
            [
                {"type": CONDITION_WEEKLY_VOLUME, "threshold_usd": 1000},
                {"type": CONDITION_WEEKLY_TX_COUNT, "threshold": 10},
            ]
        )
        self.assertEqual(conditions[0]["threshold"], 100000)
        self.assertTrue(
            conditions_met(conditions, WeekStats(volume_cents=100000, tx_count=10))
        )
        self.assertTrue(
            conditions_met(conditions, WeekStats(volume_cents=99999, tx_count=10))
        )
        self.assertTrue(
            conditions_met(conditions, WeekStats(volume_cents=100000, tx_count=9))
        )
        self.assertFalse(
            conditions_met(conditions, WeekStats(volume_cents=99999, tx_count=9))
        )

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            conditions_from_api([])

    def test_rejects_duplicate_types(self):
        with self.assertRaises(ValueError):
            conditions_from_api(
                [
                    {"type": CONDITION_WEEKLY_VOLUME, "threshold_usd": 10},
                    {"type": CONDITION_WEEKLY_VOLUME, "threshold_usd": 20},
                ]
            )

    def test_conditions_equal(self):
        a = [{"type": CONDITION_WEEKLY_VOLUME, "operator": "gte", "threshold": 100}]
        b = [{"type": CONDITION_WEEKLY_VOLUME, "operator": "gte", "threshold": 100}]
        c = [{"type": CONDITION_WEEKLY_VOLUME, "operator": "gte", "threshold": 200}]
        self.assertTrue(conditions_equal(a, b))
        self.assertFalse(conditions_equal(a, c))


class EvaluateFireOnceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _sqlite_json_for_alerts()
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            self.engine,
            tables=[DepositMethodAlert.__table__, VenmoPayment.__table__],
        )
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    async def test_fires_once_then_skips(self):
        now = datetime(2026, 9, 17, 18, 0, tzinfo=ZoneInfo("UTC"))
        session = self.Session()
        alert = DepositMethodAlert(
            name="Sonny volume",
            method="venmo",
            variant="@sonny",
            is_active=True,
            conditions=[
                {
                    "type": CONDITION_WEEKLY_VOLUME,
                    "operator": "gte",
                    "threshold": 100000,
                }
            ],
        )
        session.add(alert)
        session.commit()
        alert_id = alert.id

        with (
            patch(
                "bot.services.deposit_method_alerts.week_stats_for",
                return_value=WeekStats(volume_cents=150000, tx_count=3),
            ),
            patch(
                "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
                new=AsyncMock(return_value=True),
            ) as slack,
        ):
            fired = await evaluate_deposit_method_alerts(
                session, method="venmo", variant="@sonny", now=now
            )
            session.commit()
            self.assertEqual(fired, 1)
            self.assertEqual(slack.await_count, 1)

            fired2 = await evaluate_deposit_method_alerts(
                session, method="venmo", variant="@sonny", now=now
            )
            session.commit()
            self.assertEqual(fired2, 0)
            self.assertEqual(slack.await_count, 1)

        row = session.get(DepositMethodAlert, alert_id)
        self.assertEqual(row.last_fired_week_id, "2026-09-14")
        session.close()

    async def test_inactive_skips(self):
        now = datetime(2026, 9, 17, 18, 0, tzinfo=ZoneInfo("UTC"))
        session = self.Session()
        session.add(
            DepositMethodAlert(
                name="Off",
                method="venmo",
                variant="@sonny",
                is_active=False,
                conditions=[
                    {
                        "type": CONDITION_WEEKLY_VOLUME,
                        "operator": "gte",
                        "threshold": 100,
                    }
                ],
            )
        )
        session.commit()

        with (
            patch(
                "bot.services.deposit_method_alerts.week_stats_for",
                return_value=WeekStats(volume_cents=150000, tx_count=3),
            ),
            patch(
                "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
                new=AsyncMock(return_value=True),
            ) as slack,
        ):
            fired = await evaluate_deposit_method_alerts(
                session, method="venmo", variant="@sonny", now=now
            )
            self.assertEqual(fired, 0)
            slack.assert_not_awaited()
        session.close()

    async def test_below_threshold_skips(self):
        now = datetime(2026, 9, 17, 18, 0, tzinfo=ZoneInfo("UTC"))
        session = self.Session()
        session.add(
            DepositMethodAlert(
                name="Sonny",
                method="venmo",
                variant="@sonny",
                is_active=True,
                conditions=[
                    {
                        "type": CONDITION_WEEKLY_VOLUME,
                        "operator": "gte",
                        "threshold": 100000,
                    }
                ],
            )
        )
        session.commit()

        with (
            patch(
                "bot.services.deposit_method_alerts.week_stats_for",
                return_value=WeekStats(volume_cents=50, tx_count=1),
            ),
            patch(
                "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
                new=AsyncMock(return_value=True),
            ) as slack,
        ):
            fired = await evaluate_deposit_method_alerts(
                session, method="venmo", variant="@sonny", now=now
            )
            self.assertEqual(fired, 0)
            slack.assert_not_awaited()
        session.close()


class MaybeEvaluateAfterIngestTests(unittest.IsolatedAsyncioTestCase):
    async def test_skips_test_and_idempotent(self):
        self.assertIsNotNone(
            self
        )  # keep as instance method for IsolatedAsyncioTestCase
        with patch(
            "bot.services.deposit_method_alerts.evaluate_deposit_method_alerts",
            new=AsyncMock(),
        ) as eval_mock:
            await maybe_evaluate_after_ingest(
                method="venmo",
                variant="@x",
                created=False,
                is_test=False,
            )
            await maybe_evaluate_after_ingest(
                method="venmo",
                variant="@x",
                created=True,
                is_test=True,
            )
            eval_mock.assert_not_awaited()

    async def test_calls_evaluate_on_new_non_test(self):
        mock_session = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = mock_session
        cm.__exit__.return_value = False
        with (
            patch(
                "db.connection.get_db",
                return_value=cm,
            ),
            patch(
                "bot.services.deposit_method_alerts.evaluate_deposit_method_alerts",
                new=AsyncMock(return_value=1),
            ) as eval_mock,
        ):
            await maybe_evaluate_after_ingest(
                method="venmo",
                variant="@sonny",
                created=True,
                is_test=False,
            )
            eval_mock.assert_awaited_once()
            kwargs = eval_mock.await_args.kwargs
            self.assertEqual(kwargs["method"], "venmo")
            self.assertEqual(kwargs["variant"], "@sonny")


class DepositAlertsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        import api.auth as auth_mod

        _sqlite_json_for_alerts()
        auth_mod._SECRET = None
        self.env_patch = patch.dict(
            os.environ,
            {
                "DASHBOARD_PASSWORD": "admin-secret",
                "DASHBOARD_AM_PASSWORD": "am-secret",
            },
            clear=False,
        )
        self.env_patch.start()
        auth_mod._SECRET = None

        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            self.engine,
            tables=[
                Club.__table__,
                ClubPaymentMethod.__table__,
                ClubPaymentTier.__table__,
                ClubPaymentTierVariant.__table__,
                DepositMethodAlert.__table__,
                VenmoPayment.__table__,
            ],
        )
        self.Session = sessionmaker(bind=self.engine)
        self.app = _make_app(self.Session)
        self.client = TestClient(self.app)
        self.token = create_token(ROLE_ADMIN)

        session = self.Session()
        session.add(
            VenmoPayment(
                method_owner="round-table",
                payer_name="A",
                amount_cents=5000,
                venmo_handle="@sonny",
                goods_or_services=False,
                is_test=False,
            )
        )
        session.commit()
        session.close()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.engine.dispose()

    def _auth(self):
        return {"Authorization": f"Bearer {self.token}"}

    def test_create_rejects_empty_conditions(self):
        res = self.client.post(
            "/api/deposit-alerts",
            headers=self._auth(),
            json={
                "name": "Bad",
                "method": "venmo",
                "variant": "@sonny",
                "conditions": [],
            },
        )
        self.assertEqual(res.status_code, 400)

    def test_create_and_list(self):
        with patch(
            "api.routes.deposit_method_alerts.evaluate_deposit_method_alerts",
            new=AsyncMock(return_value=0),
        ):
            res = self.client.post(
                "/api/deposit-alerts",
                headers=self._auth(),
                json={
                    "name": "Sonny $1k",
                    "method": "venmo",
                    "variant": "@sonny",
                    "conditions": [
                        {
                            "type": "weekly_volume",
                            "threshold_usd": 1000,
                        }
                    ],
                },
            )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["name"], "Sonny $1k")
        self.assertEqual(body["conditions"][0]["threshold"], 100000)
        self.assertTrue(body["is_active"])

        listed = self.client.get("/api/deposit-alerts", headers=self._auth())
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        self.assertEqual(listed.json()[0]["week_tx_count"], 1)

    def test_variants_excludes_test(self):
        session = self.Session()
        session.add(
            VenmoPayment(
                method_owner="round-table",
                payer_name="T",
                amount_cents=100,
                venmo_handle="@testonly",
                goods_or_services=False,
                is_test=True,
            )
        )
        session.commit()
        session.close()

        res = self.client.get(
            "/api/deposit-alerts/variants?method=venmo",
            headers=self._auth(),
        )
        self.assertEqual(res.status_code, 200)
        items = res.json()["items"]
        self.assertIn("@sonny", items)
        self.assertNotIn("@testonly", items)

    def test_patch_clears_fired_on_condition_change(self):
        session = self.Session()
        row = DepositMethodAlert(
            name="X",
            method="venmo",
            variant="@sonny",
            is_active=True,
            conditions=[
                {
                    "type": CONDITION_WEEKLY_VOLUME,
                    "operator": "gte",
                    "threshold": 100000,
                }
            ],
            last_fired_week_id="2026-09-14",
        )
        session.add(row)
        session.commit()
        alert_id = row.id
        session.close()

        with patch(
            "api.routes.deposit_method_alerts.evaluate_deposit_method_alerts",
            new=AsyncMock(return_value=0),
        ):
            res = self.client.patch(
                f"/api/deposit-alerts/{alert_id}",
                headers=self._auth(),
                json={
                    "conditions": [
                        {"type": "weekly_volume", "threshold_usd": 2000},
                    ]
                },
            )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIsNone(res.json()["last_fired_week_id"])

    def test_delete(self):
        session = self.Session()
        row = DepositMethodAlert(
            name="X",
            method="venmo",
            variant="@sonny",
            is_active=True,
            conditions=[
                {
                    "type": CONDITION_WEEKLY_TX_COUNT,
                    "operator": "gte",
                    "threshold": 1,
                }
            ],
        )
        session.add(row)
        session.commit()
        alert_id = row.id
        session.close()

        res = self.client.delete(
            f"/api/deposit-alerts/{alert_id}",
            headers=self._auth(),
        )
        self.assertEqual(res.status_code, 204)


def _volume(cents: int) -> dict:
    return {
        "type": CONDITION_WEEKLY_VOLUME,
        "operator": "gte",
        "threshold": cents,
    }


def _count(n: int) -> dict:
    return {
        "type": CONDITION_WEEKLY_TX_COUNT,
        "operator": "gte",
        "threshold": n,
    }


NOW = datetime(2026, 9, 17, 18, 0, tzinfo=ZoneInfo("UTC"))
WEEK = "2026-09-14"
NEXT_WEEK = datetime(2026, 9, 21, 15, 0, tzinfo=ZoneInfo("UTC"))


class DisableConditionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _sqlite_json_for_alerts()
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine, tables=[DepositMethodAlert.__table__])
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_sets_are_independent(self):
        stats = WeekStats(volume_cents=150_000, tx_count=2)
        alert = DepositMethodAlert(
            name="Split",
            method="venmo",
            variant="@sonny",
            is_active=True,
            conditions=[_volume(100_000)],
            disable_enabled=True,
            disable_conditions=[_count(10)],
        )
        self.assertTrue(conditions_met(alert.conditions, stats))
        self.assertFalse(conditions_met(alert.disable_conditions, stats))
        self.assertEqual(disable_status(alert, stats, WEEK), "on")

    def test_disable_set_is_or(self):
        alert = DepositMethodAlert(
            name="Or",
            method="venmo",
            variant="@sonny",
            is_active=True,
            conditions=[_count(99)],
            disable_enabled=True,
            disable_conditions=[_volume(100_000), _count(10)],
        )
        self.assertTrue(
            conditions_met(
                alert.disable_conditions, WeekStats(volume_cents=50, tx_count=10)
            )
        )
        self.assertFalse(
            conditions_met(
                alert.disable_conditions, WeekStats(volume_cents=50, tx_count=1)
            )
        )

    async def test_unchecked_ignores_met_disable_conditions(self):
        session = self.Session()
        session.add(
            DepositMethodAlert(
                name="Open",
                method="venmo",
                variant="@sonny",
                is_active=True,
                conditions=[_count(99)],
                disable_enabled=False,
                disable_conditions=[_volume(100)],
            )
        )
        session.commit()
        with (
            patch(
                "bot.services.deposit_method_alerts.week_stats_for",
                return_value=WeekStats(volume_cents=500_000, tx_count=1),
            ),
            patch(
                "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
                new=AsyncMock(return_value=True),
            ) as slack,
        ):
            fired = await evaluate_deposit_method_alerts(
                session, method="venmo", variant="@sonny", now=NOW
            )
        self.assertEqual(fired, 0)
        slack.assert_not_awaited()
        row = session.query(DepositMethodAlert).one()
        self.assertFalse(
            destination_is_disabled(
                row, WeekStats(volume_cents=500_000, tx_count=1), WEEK
            )
        )
        session.close()

    async def test_inactive_does_not_slack_or_disable(self):
        session = self.Session()
        session.add(
            DepositMethodAlert(
                name="Off",
                method="venmo",
                variant="@sonny",
                is_active=False,
                conditions=[_volume(100)],
                disable_enabled=True,
                disable_conditions=[_volume(100)],
                last_disable_fired_week_id=WEEK,
            )
        )
        session.commit()
        stats = WeekStats(volume_cents=500_000, tx_count=3)
        row = session.query(DepositMethodAlert).one()
        self.assertIsNone(disable_status(row, stats, WEEK))
        with (
            patch(
                "bot.services.deposit_method_alerts.week_stats_for",
                return_value=stats,
            ),
            patch(
                "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
                new=AsyncMock(return_value=True),
            ) as slack,
        ):
            fired = await evaluate_deposit_method_alerts(
                session, method="venmo", variant="@sonny", now=NOW
            )
        self.assertEqual(fired, 0)
        slack.assert_not_awaited()
        session.close()


class DisableSlackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _sqlite_json_for_alerts()
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine, tables=[DepositMethodAlert.__table__])
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _alert(self, **kwargs) -> DepositMethodAlert:
        session = self.Session()
        row = DepositMethodAlert(
            name=kwargs.pop("name", "Sonny"),
            method="venmo",
            variant="@sonny",
            is_active=True,
            conditions=kwargs.pop("conditions", [_volume(100_000)]),
            disable_enabled=kwargs.pop("disable_enabled", False),
            disable_conditions=kwargs.pop("disable_conditions", []),
            **kwargs,
        )
        session.add(row)
        session.commit()
        session.close()
        return row

    async def _eval(self, stats: WeekStats, slack_ok: bool = True, now=NOW):
        session = self.Session()
        with (
            patch(
                "bot.services.deposit_method_alerts.week_stats_for",
                return_value=stats,
            ),
            patch(
                "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
                new=AsyncMock(return_value=slack_ok),
            ) as slack,
        ):
            fired = await evaluate_deposit_method_alerts(
                session, method="venmo", variant="@sonny", now=now
            )
            session.commit()
        row = session.query(DepositMethodAlert).one()
        text = ""
        if slack.await_count:
            text = slack.await_args.args[0]
        session.close()
        return fired, slack.await_count, text, row

    async def test_alert_only_leaves_destination_offered(self):
        self._alert(disable_enabled=True, disable_conditions=[_count(50)])
        fired, posts, text, row = await self._eval(WeekStats(150_000, 3))
        self.assertEqual(fired, 1)
        self.assertEqual(posts, 1)
        self.assertIn("Conditions:", text)
        self.assertNotIn("off in every club", text)
        self.assertEqual(row.last_fired_week_id, WEEK)
        self.assertIsNone(row.last_disable_fired_week_id)
        self.assertFalse(destination_is_disabled(row, WeekStats(150_000, 3), WEEK))

    async def test_disable_only_skips_after_slack_success(self):
        self._alert(
            conditions=[_count(99)],
            disable_enabled=True,
            disable_conditions=[_volume(100_000)],
        )
        fired, posts, text, row = await self._eval(WeekStats(150_000, 1))
        self.assertEqual(fired, 1)
        self.assertEqual(posts, 1)
        self.assertIn("off in every club", text)
        self.assertEqual(row.last_disable_fired_week_id, WEEK)
        self.assertIsNone(row.last_fired_week_id)
        self.assertTrue(destination_is_disabled(row, WeekStats(150_000, 1), WEEK))

    async def test_both_one_post_sets_both_latches(self):
        self._alert(disable_enabled=True, disable_conditions=[_count(2)])
        fired, posts, text, row = await self._eval(WeekStats(150_000, 3))
        self.assertEqual(fired, 1)
        self.assertEqual(posts, 1)
        self.assertIn("Conditions:", text)
        self.assertIn("off in every club", text)
        self.assertEqual(row.last_fired_week_id, WEEK)
        self.assertEqual(row.last_disable_fired_week_id, WEEK)

    async def test_disable_slack_failure_does_not_latch(self):
        self._alert(
            conditions=[_count(99)],
            disable_enabled=True,
            disable_conditions=[_volume(100)],
        )
        stats = WeekStats(500_000, 1)
        fired, posts, _text, row = await self._eval(stats, slack_ok=False)
        self.assertEqual(fired, 0)
        self.assertEqual(posts, 1)
        self.assertIsNone(row.last_disable_fired_week_id)
        self.assertFalse(destination_is_disabled(row, stats, WEEK))
        fired2, posts2, _text2, row2 = await self._eval(stats, slack_ok=True)
        self.assertEqual(fired2, 1)
        self.assertEqual(posts2, 1)
        self.assertTrue(destination_is_disabled(row2, stats, WEEK))

    async def test_combined_slack_failure_sets_neither_latch(self):
        self._alert(disable_enabled=True, disable_conditions=[_volume(100)])
        stats = WeekStats(500_000, 3)
        fired, _posts, text, row = await self._eval(stats, slack_ok=False)
        self.assertEqual(fired, 0)
        self.assertIn("Conditions:", text)
        self.assertIn("off in every club", text)
        self.assertIsNone(row.last_fired_week_id)
        self.assertIsNone(row.last_disable_fired_week_id)
        self.assertFalse(destination_is_disabled(row, stats, WEEK))
        fired2, _posts2, _text2, row2 = await self._eval(stats, slack_ok=True)
        self.assertEqual(fired2, 1)
        self.assertEqual(row2.last_fired_week_id, WEEK)
        self.assertEqual(row2.last_disable_fired_week_id, WEEK)

    async def test_later_disable_is_its_own_post(self):
        self._alert(disable_enabled=True, disable_conditions=[_count(10)])
        await self._eval(WeekStats(150_000, 1))
        fired, posts, text, row = await self._eval(WeekStats(150_000, 10))
        self.assertEqual(fired, 1)
        self.assertEqual(posts, 1)
        self.assertIn("off in every club", text)
        self.assertNotIn("\nConditions:\n", text)
        self.assertEqual(row.last_disable_fired_week_id, WEEK)
        fired3, posts3, _text3, _row3 = await self._eval(WeekStats(150_000, 12))
        self.assertEqual(fired3, 0)
        self.assertEqual(posts3, 0)

    async def test_later_alert_is_its_own_post(self):
        self._alert(
            conditions=[_count(10)],
            disable_enabled=True,
            disable_conditions=[_volume(100_000)],
        )
        await self._eval(WeekStats(150_000, 1))
        fired, posts, text, row = await self._eval(WeekStats(150_000, 10))
        self.assertEqual(fired, 1)
        self.assertEqual(posts, 1)
        self.assertIn("Conditions:", text)
        self.assertNotIn("off in every club", text)
        self.assertEqual(row.last_fired_week_id, WEEK)
        self.assertEqual(row.last_disable_fired_week_id, WEEK)

    async def test_second_disable_evaluation_does_not_repost(self):
        self._alert(
            conditions=[_count(99)],
            disable_enabled=True,
            disable_conditions=[_volume(100)],
        )
        await self._eval(WeekStats(500_000, 1))
        fired, posts, _text, row = await self._eval(WeekStats(600_000, 2))
        self.assertEqual(fired, 0)
        self.assertEqual(posts, 0)
        self.assertEqual(row.last_disable_fired_week_id, WEEK)


class DisableReenableTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _sqlite_json_for_alerts()
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine, tables=[DepositMethodAlert.__table__])
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _latched(self, **kwargs) -> DepositMethodAlert:
        session = self.Session()
        row = DepositMethodAlert(
            name="Sonny",
            method="venmo",
            variant="@sonny",
            is_active=True,
            conditions=[_count(99)],
            disable_enabled=True,
            disable_conditions=[_volume(100_000)],
            last_disable_fired_week_id=WEEK,
            **kwargs,
        )
        session.add(row)
        session.commit()
        session.close()
        return row

    async def test_uncheck_offers_again_recheck_skips_without_slack(self):
        self._latched()
        stats = WeekStats(150_000, 1)
        session = self.Session()
        row = session.query(DepositMethodAlert).one()
        row.disable_enabled = False
        session.commit()
        self.assertFalse(destination_is_disabled(row, stats, WEEK))
        row.disable_enabled = True
        session.commit()
        self.assertTrue(destination_is_disabled(row, stats, WEEK))
        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new=AsyncMock(return_value=True),
        ) as slack:
            with patch(
                "bot.services.deposit_method_alerts.week_stats_for",
                return_value=stats,
            ):
                fired = await evaluate_deposit_method_alerts(
                    session, method="venmo", variant="@sonny", now=NOW
                )
        self.assertEqual(fired, 0)
        slack.assert_not_awaited()
        session.close()

    async def test_raising_threshold_offers_again_lowering_skips_without_slack(self):
        self._latched()
        session = self.Session()
        row = session.query(DepositMethodAlert).one()
        high = WeekStats(150_000, 1)
        row.disable_conditions = [_volume(500_000)]
        session.commit()
        self.assertFalse(destination_is_disabled(row, high, WEEK))
        self.assertEqual(disable_status(row, high, WEEK), "on")
        row.disable_conditions = [_volume(100_000)]
        session.commit()
        self.assertTrue(destination_is_disabled(row, high, WEEK))
        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new=AsyncMock(return_value=True),
        ) as slack:
            with patch(
                "bot.services.deposit_method_alerts.week_stats_for",
                return_value=high,
            ):
                fired = await evaluate_deposit_method_alerts(
                    session, method="venmo", variant="@sonny", now=NOW
                )
        self.assertEqual(fired, 0)
        slack.assert_not_awaited()
        session.close()

    async def test_deactivate_offers_again_reactivate_skips_without_slack(self):
        self._latched()
        stats = WeekStats(150_000, 1)
        session = self.Session()
        row = session.query(DepositMethodAlert).one()
        row.is_active = False
        session.commit()
        self.assertFalse(destination_is_disabled(row, stats, WEEK))
        row.is_active = True
        session.commit()
        self.assertTrue(destination_is_disabled(row, stats, WEEK))
        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new=AsyncMock(return_value=True),
        ) as slack:
            with patch(
                "bot.services.deposit_method_alerts.week_stats_for",
                return_value=stats,
            ):
                fired = await evaluate_deposit_method_alerts(
                    session, method="venmo", variant="@sonny", now=NOW
                )
        self.assertEqual(fired, 0)
        slack.assert_not_awaited()
        session.close()

    async def test_new_week_requires_a_new_slack_post(self):
        self._latched()
        stats = WeekStats(150_000, 1)
        session = self.Session()
        row = session.query(DepositMethodAlert).one()
        self.assertFalse(
            destination_is_disabled(
                row, stats, eastern_week_bounds_utc(NEXT_WEEK).week_id
            )
        )
        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new=AsyncMock(return_value=False),
        ):
            with patch(
                "bot.services.deposit_method_alerts.week_stats_for",
                return_value=stats,
            ):
                await evaluate_deposit_method_alerts(
                    session, method="venmo", variant="@sonny", now=NEXT_WEEK
                )
                session.commit()
        row = session.query(DepositMethodAlert).one()
        self.assertEqual(row.last_disable_fired_week_id, WEEK)
        self.assertFalse(
            destination_is_disabled(
                row, stats, eastern_week_bounds_utc(NEXT_WEEK).week_id
            )
        )
        with patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new=AsyncMock(return_value=True),
        ):
            with patch(
                "bot.services.deposit_method_alerts.week_stats_for",
                return_value=stats,
            ):
                await evaluate_deposit_method_alerts(
                    session, method="venmo", variant="@sonny", now=NEXT_WEEK
                )
                session.commit()
        row = session.query(DepositMethodAlert).one()
        self.assertEqual(
            row.last_disable_fired_week_id, eastern_week_bounds_utc(NEXT_WEEK).week_id
        )
        session.close()


class DisablePickTests(unittest.TestCase):
    def test_venmo_sticky_disabled_falls_back_and_keeps_weight(self):
        sticky = SimpleNamespace(destination_tag="@paused", variant_id=99)
        paused = {
            "variant_id": 99,
            "weight": 100,
            "venmo_tag": "@paused",
            "response_type": "text",
            "response_text": "https://venmo.com/u/paused",
            "use_group_checkout_link": False,
        }
        other = {
            "variant_id": 2,
            "weight": 80,
            "venmo_tag": "@active",
            "response_type": "text",
            "response_text": "https://venmo.com/u/active",
            "use_group_checkout_link": False,
        }
        with (
            patch.object(
                dep, "get_tier_for_amount", return_value={"id": 2, "label": "Over"}
            ),
            patch.object(dep, "list_tier_variants", return_value=[paused, other]),
            patch.object(dep, "get_destination_stickiness", return_value=sticky),
            patch.object(dep, "get_chat_binding", return_value=None),
            patch.object(dep, "list_method_variants", return_value=[]),
            patch.object(
                dep,
                "_pick_weighted_variant_dicts",
                side_effect=lambda rows: dict(rows[0]),
            ),
            patch(
                "bot.services.deposit_method_alerts.disabled_destination_keys",
                return_value={destination_match_key("venmo", "@paused")},
            ),
            patch("db.connection.get_db") as mock_get_db,
        ):
            cm = MagicMock()
            cm.__enter__.return_value = MagicMock()
            cm.__exit__.return_value = False
            mock_get_db.return_value = cm
            response, _tier = dep._pick_deposit_variant_response(
                4,
                {"id": 4, "name": "Venmo", "slug": "venmo"},
                Decimal("150"),
                chat_id=-100,
                method_slug="venmo",
            )
        self.assertEqual(response.get("variant_id"), 2)
        self.assertNotIn(dep._STICKINESS_FALLBACK_KEY, response)
        self.assertEqual(paused["weight"], 100)

    def test_venmo_disabled_with_no_other_variant_hides_method(self):
        paused = {
            "variant_id": 99,
            "weight": 100,
            "venmo_tag": "@paused",
            "response_type": "text",
            "response_text": "https://venmo.com/u/paused",
            "use_group_checkout_link": False,
        }
        with (
            patch.object(
                dep, "get_tier_for_amount", return_value={"id": 2, "label": "Over"}
            ),
            patch.object(dep, "list_tier_variants", return_value=[paused]),
            patch.object(
                dep,
                "get_destination_stickiness",
                return_value=SimpleNamespace(destination_tag="@paused", variant_id=99),
            ),
            patch.object(dep, "list_method_variants", return_value=[]),
            patch(
                "bot.services.deposit_method_alerts.disabled_destination_keys",
                return_value={destination_match_key("venmo", "@paused")},
            ),
            patch("db.connection.get_db") as mock_get_db,
        ):
            cm = MagicMock()
            cm.__enter__.return_value = MagicMock()
            cm.__exit__.return_value = False
            mock_get_db.return_value = cm
            response, _tier = dep._pick_deposit_variant_response(
                4,
                {"id": 4, "name": "Venmo", "slug": "venmo"},
                Decimal("150"),
                chat_id=-100,
                method_slug="venmo",
            )
        self.assertIsNone(response)
        self.assertEqual(paused["weight"], 100)

    def test_zelle_sticky_disabled_falls_back(self):
        sticky = SimpleNamespace(
            id=99,
            weight=100,
            method_id=4,
            tier_id=10,
            label="Old",
            response_type="text",
            response_text="Zelle: pay@example.com",
            response_file_id=None,
            response_caption=None,
            use_group_checkout_link=None,
            group_checkout_provider=None,
            hyperlink_text=None,
            checkout_min_amount=None,
            checkout_max_amount=None,
            venmo_tag=None,
            cashapp_tag=None,
        )
        other = SimpleNamespace(
            **{
                **sticky.__dict__,
                "id": 2,
                "response_text": "Zelle: other@example.com",
                "label": "New",
            }
        )
        method = SimpleNamespace(id=4, slug="zelle")
        session = MagicMock()
        session.query.return_value.get.side_effect = [method, sticky]
        session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = [
            sticky,
            other,
        ]
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False
        with (
            patch("bot.services.club_payment_v2.get_db", return_value=cm),
            patch(
                "bot.services.deposit_method_alerts.disabled_destination_keys",
                return_value={destination_match_key("zelle", "pay@example.com")},
            ),
            patch(
                "bot.services.club_payment_v2.random.choices",
                return_value=[other],
            ) as choices,
        ):
            result = club_payment_v2.pick_variant(4, tier_id=10, variant_id=99)
        choices.assert_called_once()
        picked = choices.call_args[0][0]
        self.assertEqual([row.id for row in picked], [2])
        self.assertEqual(result["variant_id"], 2)
        self.assertEqual(sticky.weight, 100)

    def test_missing_sticky_variant_does_not_fall_through(self):
        method = SimpleNamespace(id=4, slug="zelle")
        session = MagicMock()
        session.query.return_value.get.side_effect = [method, None]
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False
        with (
            patch("bot.services.club_payment_v2.get_db", return_value=cm),
            patch(
                "bot.services.deposit_method_alerts.disabled_destination_keys",
                return_value=set(),
            ),
        ):
            self.assertIsNone(
                club_payment_v2.pick_variant(4, tier_id=10, variant_id=99)
            )

    def test_missing_disable_column_leaves_destinations_available(self):
        session = MagicMock()
        session.query.return_value.filter.return_value.all.side_effect = (
            ProgrammingError(
                "SELECT",
                {},
                Exception(
                    "column deposit_method_alerts.disable_enabled does not exist"
                ),
            )
        )
        self.assertEqual(disabled_destination_keys(session, "venmo"), set())
        session.rollback.assert_called_once()

    def test_same_handle_matches_every_club_other_handle_does_not(self):
        paused = SimpleNamespace(
            venmo_tag="@paused", response_text="", response_caption=""
        )
        other = SimpleNamespace(
            venmo_tag="@active", response_text="", response_caption=""
        )
        keys = {destination_match_key("venmo", "@Paused")}
        self.assertTrue(is_destination_disabled("venmo", paused, keys))
        self.assertTrue(is_destination_disabled("venmo", paused, keys))
        self.assertFalse(is_destination_disabled("venmo", other, keys))

    def test_ingested_payment_still_counts_while_destination_is_disabled(self):
        _sqlite_json_for_alerts()
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(
            engine, tables=[DepositMethodAlert.__table__, VenmoPayment.__table__]
        )
        Session = sessionmaker(bind=engine)
        session = Session()
        session.add(
            VenmoPayment(
                method_owner="round-table",
                payer_name="A",
                amount_cents=2500,
                venmo_handle="@sonny",
                goods_or_services=False,
                is_test=False,
                created_at=NOW,
            )
        )
        alert = DepositMethodAlert(
            name="Sonny",
            method="venmo",
            variant="@sonny",
            is_active=True,
            conditions=[_count(99)],
            disable_enabled=True,
            disable_conditions=[_volume(100)],
            last_disable_fired_week_id=WEEK,
        )
        session.add(alert)
        session.commit()
        from bot.services.deposit_method_alerts import week_stats_for

        stats = week_stats_for(
            session, method="venmo", variant="@sonny", week=eastern_week_bounds_utc(NOW)
        )
        self.assertGreaterEqual(stats.tx_count, 1)
        self.assertTrue(destination_is_disabled(alert, stats, WEEK))
        session.close()
        engine.dispose()


class DisableCardTests(unittest.TestCase):
    def setUp(self) -> None:
        import api.auth as auth_mod

        _sqlite_json_for_alerts()
        auth_mod._SECRET = None
        self.env_patch = patch.dict(
            os.environ,
            {
                "DASHBOARD_PASSWORD": "admin-secret",
                "DASHBOARD_AM_PASSWORD": "am-secret",
            },
            clear=False,
        )
        self.env_patch.start()
        auth_mod._SECRET = None
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            self.engine,
            tables=[
                Club.__table__,
                ClubPaymentMethod.__table__,
                ClubPaymentTier.__table__,
                ClubPaymentTierVariant.__table__,
                DepositMethodAlert.__table__,
                VenmoPayment.__table__,
            ],
        )
        self.Session = sessionmaker(bind=self.engine)
        self.client = TestClient(_make_app(self.Session))
        self.token = create_token(ROLE_ADMIN)

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.engine.dispose()

    def _auth(self):
        return {"Authorization": f"Bearer {self.token}"}

    def test_create_and_update_reject_empty_disable_conditions(self):
        res = self.client.post(
            "/api/deposit-alerts",
            headers=self._auth(),
            json={
                "name": "Bad",
                "method": "venmo",
                "variant": "@sonny",
                "conditions": [{"type": "weekly_volume", "threshold_usd": 10}],
                "disable_enabled": True,
                "disable_conditions": [],
            },
        )
        self.assertEqual(res.status_code, 400)
        session = self.Session()
        row = DepositMethodAlert(
            name="X",
            method="venmo",
            variant="@sonny",
            is_active=True,
            conditions=[_volume(1000)],
        )
        session.add(row)
        session.commit()
        alert_id = row.id
        session.close()
        with patch(
            "api.routes.deposit_method_alerts.evaluate_deposit_method_alerts",
            new=AsyncMock(return_value=0),
        ):
            patched = self.client.patch(
                f"/api/deposit-alerts/{alert_id}",
                headers=self._auth(),
                json={"disable_enabled": True},
            )
        self.assertEqual(patched.status_code, 400)

    def test_variant_change_clears_disable_latch_threshold_change_does_not(self):
        session = self.Session()
        row = DepositMethodAlert(
            name="X",
            method="venmo",
            variant="@sonny",
            is_active=True,
            conditions=[_volume(1000)],
            disable_enabled=True,
            disable_conditions=[_volume(5000)],
            last_disable_fired_week_id=WEEK,
        )
        session.add(row)
        session.commit()
        alert_id = row.id
        session.close()
        with patch(
            "api.routes.deposit_method_alerts.evaluate_deposit_method_alerts",
            new=AsyncMock(return_value=0),
        ):
            kept = self.client.patch(
                f"/api/deposit-alerts/{alert_id}",
                headers=self._auth(),
                json={
                    "disable_conditions": [
                        {"type": "weekly_volume", "threshold_usd": 80},
                    ]
                },
            )
        self.assertEqual(kept.status_code, 200, kept.text)
        self.assertEqual(kept.json()["last_disable_fired_week_id"], WEEK)
        with patch(
            "api.routes.deposit_method_alerts.evaluate_deposit_method_alerts",
            new=AsyncMock(return_value=0),
        ):
            cleared = self.client.patch(
                f"/api/deposit-alerts/{alert_id}",
                headers=self._auth(),
                json={"variant": "@other"},
            )
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertIsNone(cleared.json()["last_disable_fired_week_id"])

    def test_list_status_and_clubs(self):
        session = self.Session()
        session.add(Club(name="Round Table", telegram_user_id=1))
        session.add(Club(name="Aces", telegram_user_id=2))
        session.commit()
        clubs = {c.name: c.id for c in session.query(Club).all()}
        for club_name, tag in (("Round Table", "@sonny"), ("Aces", "@sonny")):
            method = ClubPaymentMethod(
                club_id=clubs[club_name],
                direction="deposit",
                name="Venmo",
                slug="venmo",
            )
            session.add(method)
            session.flush()
            tier = ClubPaymentTier(method_id=method.id, label="All")
            session.add(tier)
            session.flush()
            session.add(
                ClubPaymentTierVariant(
                    method_id=method.id,
                    tier_id=tier.id,
                    label=tag,
                    venmo_tag=tag,
                )
            )
        week = eastern_week_bounds_utc().week_id
        session.add_all(
            [
                DepositMethodAlert(
                    name="Unchecked",
                    method="venmo",
                    variant="@sonny",
                    is_active=True,
                    conditions=[_volume(100)],
                    disable_enabled=False,
                ),
                DepositMethodAlert(
                    name="Under",
                    method="venmo",
                    variant="@quiet",
                    is_active=True,
                    conditions=[_count(99)],
                    disable_enabled=True,
                    disable_conditions=[_volume(10**9)],
                ),
                DepositMethodAlert(
                    name="Waiting",
                    method="venmo",
                    variant="@wait",
                    is_active=True,
                    conditions=[_count(99)],
                    disable_enabled=True,
                    disable_conditions=[_volume(1)],
                ),
                DepositMethodAlert(
                    name="Off",
                    method="venmo",
                    variant="@sonny",
                    is_active=True,
                    conditions=[_count(99)],
                    disable_enabled=True,
                    disable_conditions=[_volume(1)],
                    last_disable_fired_week_id=week,
                ),
            ]
        )
        session.add(
            VenmoPayment(
                method_owner="round-table",
                payer_name="A",
                amount_cents=5000,
                venmo_handle="@sonny",
                goods_or_services=False,
                is_test=False,
            )
        )
        session.add(
            VenmoPayment(
                method_owner="round-table",
                payer_name="B",
                amount_cents=5000,
                venmo_handle="@wait",
                goods_or_services=False,
                is_test=False,
            )
        )
        session.commit()
        session.close()

        listed = self.client.get("/api/deposit-alerts", headers=self._auth())
        self.assertEqual(listed.status_code, 200, listed.text)
        by_name = {row["name"]: row for row in listed.json()}
        self.assertIsNone(by_name["Unchecked"]["disable_status"])
        self.assertEqual(by_name["Under"]["disable_status"], "on")
        self.assertEqual(by_name["Waiting"]["disable_status"], "pending_slack")
        self.assertEqual(by_name["Off"]["disable_status"], "off")
        club_names = {club["name"] for club in by_name["Off"]["clubs"]}
        self.assertEqual(club_names, {"Aces", "Round Table"})
        self.assertEqual(by_name["Under"]["clubs"], [])

    def test_either_alert_off_skips_the_destination(self):
        _sqlite_json_for_alerts()
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[DepositMethodAlert.__table__])
        Session = sessionmaker(bind=engine)
        session = Session()
        week = eastern_week_bounds_utc(NOW).week_id
        session.add_all(
            [
                DepositMethodAlert(
                    name="Open",
                    method="venmo",
                    variant="@sonny",
                    is_active=True,
                    conditions=[_count(99)],
                    disable_enabled=True,
                    disable_conditions=[_volume(10**9)],
                ),
                DepositMethodAlert(
                    name="Closed",
                    method="venmo",
                    variant="@sonny",
                    is_active=True,
                    conditions=[_count(99)],
                    disable_enabled=True,
                    disable_conditions=[_volume(100)],
                    last_disable_fired_week_id=week,
                ),
            ]
        )
        session.commit()
        with patch(
            "bot.services.deposit_method_alerts.week_stats_for",
            return_value=WeekStats(500_000, 1),
        ):
            keys = disabled_destination_keys(session, "venmo", now=NOW)
        self.assertIn(destination_match_key("venmo", "@sonny"), keys)
        rows = session.query(DepositMethodAlert).order_by(DepositMethodAlert.id).all()
        stats = WeekStats(500_000, 1)
        self.assertEqual(disable_status(rows[0], stats, week), "on")
        self.assertEqual(disable_status(rows[1], stats, week), "off")
        session.close()
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
