"""Deposit method weekly alerts: conditions, week bounds, fire-once, API."""

from __future__ import annotations

import os
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.auth import ROLE_ADMIN, create_token
from api.routes.deposit_method_alerts import router
from bot.services.deposit_method_alerts import (
    CONDITION_WEEKLY_TX_COUNT,
    CONDITION_WEEKLY_VOLUME,
    WeekStats,
    conditions_equal,
    conditions_from_api,
    conditions_met,
    eastern_week_bounds_utc,
    evaluate_deposit_method_alerts,
    maybe_evaluate_after_ingest,
)
from db.connection import get_db_dependency
from db.models import Base, DepositMethodAlert, VenmoPayment

EASTERN = ZoneInfo("America/New_York")


def _sqlite_json_for_alerts() -> None:
    # SQLite cannot compile PostgreSQL JSONB; use JSON for in-memory tests only.
    DepositMethodAlert.__table__.c.conditions.type = JSON()


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
    def test_volume_and_count_gte(self):
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
        self.assertFalse(
            conditions_met(conditions, WeekStats(volume_cents=99999, tx_count=10))
        )
        self.assertFalse(
            conditions_met(conditions, WeekStats(volume_cents=100000, tx_count=9))
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
            tables=[DepositMethodAlert.__table__, VenmoPayment.__table__],
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


if __name__ == "__main__":
    unittest.main()
