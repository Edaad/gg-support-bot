"""Response audit API: token auth, verdict upsert, status / candidates / weekly."""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.response_audit import TOKEN_ENV, router
from db.connection import get_db_dependency
from db.models import (
    Club,
    GroupChatDailyActivity,
    GroupChatDailyTranscript,
    ResponseAuditRun,
    ResponseAuditVerdict,
    ResponseEvent,
)
from tests.support.sqlite_db import get_db_for, make_sqlite_session_factory

TOKEN = "audit-secret"
HEADERS = {"X-Audit-Token": TOKEN}
DAY = date(2026, 9, 29)  # Tuesday
TABLES = [
    "clubs",
    "escalation_episodes",
    "escalation_events",
    "group_chat_daily_activity",
    "group_chat_daily_transcripts",
    "response_events",
    "response_audit_verdicts",
    "response_audit_runs",
]


def _utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


class ResponseAuditApiTests(unittest.TestCase):
    def setUp(self):
        self.factory = make_sqlite_session_factory(TABLES)
        app = FastAPI()
        app.include_router(router)

        def override_db():
            session = self.factory()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        app.dependency_overrides[get_db_dependency] = override_db
        self.client = TestClient(app)
        self._env = patch.dict(os.environ, {TOKEN_ENV: TOKEN})
        self._env.start()
        self._seed()

    def tearDown(self):
        self._env.stop()

    def _seed(self):
        with self.factory() as s:
            s.add(Club(id=1, name="Round Table", telegram_user_id=333))
            s.flush()
            for chat_id in (-1001, -1002, -1003):
                s.add(
                    GroupChatDailyActivity(
                        activity_date=DAY,
                        chat_id=chat_id,
                        club_id=1,
                        non_bot_message_count=3,
                        first_message_at=_utc(2026, 9, 29, 14),
                        last_message_at=_utc(2026, 9, 29, 15),
                    )
                )
            s.add(
                GroupChatDailyTranscript(
                    activity_date=DAY, chat_id=-1001, club_id=1, status="complete"
                )
            )
            s.add(
                GroupChatDailyTranscript(
                    activity_date=DAY,
                    chat_id=-1002,
                    club_id=1,
                    status="failed",
                    error="FloodWait",
                    attempt_count=2,
                )
            )
            # Tue 10:00 ET slow, 10:30 ET fast, 11:00 ET unanswered.
            for secs, cand, hour in (
                (600, True, 14),
                (60, False, 14),
                (None, True, 15),
            ):
                s.add(
                    ResponseEvent(
                        activity_date=DAY,
                        chat_id=-1001,
                        club_id=1,
                        group_title="RT / 1 / Sam",
                        start_kind="player_question",
                        clock_start_at=_utc(2026, 9, 29, hour, 30 if not cand else 0),
                        clock_start_msg_id=hour * 100 + (secs or 1),
                        response_seconds=secs,
                        is_candidate=cand,
                        pre_labels={"gratitude_only": False},
                        excerpt=[{"id": 1, "text": "hi"}] if cand else None,
                        rule_version="ra-1",
                    )
                )
            s.add(
                ResponseAuditRun(
                    activity_date=DAY, ran_at=_utc(2026, 9, 30, 7), rule_version="ra-1"
                )
            )
            s.commit()
            self.event_ids = [
                r.id for r in s.query(ResponseEvent).order_by(ResponseEvent.id)
            ]

    # ── auth ────────────────────────────────────────────────────────────

    def test_missing_or_wrong_token_is_401(self):
        r = self.client.get(f"/api/response-audit/status?date={DAY}")
        self.assertEqual(r.status_code, 401)
        r = self.client.get(
            f"/api/response-audit/status?date={DAY}",
            headers={"X-Audit-Token": "nope"},
        )
        self.assertEqual(r.status_code, 401)

    def test_dashboard_jwt_is_not_accepted(self):
        from api.auth import create_token

        r = self.client.get(
            f"/api/response-audit/status?date={DAY}",
            headers={"Authorization": f"Bearer {create_token()}"},
        )
        self.assertEqual(r.status_code, 401)

    def test_unset_token_env_is_503(self):
        with patch.dict(os.environ, {TOKEN_ENV: ""}):
            r = self.client.get(
                f"/api/response-audit/status?date={DAY}", headers=HEADERS
            )
        self.assertEqual(r.status_code, 503)

    # ── reads ───────────────────────────────────────────────────────────

    def test_status(self):
        r = self.client.get(f"/api/response-audit/status?date={DAY}", headers=HEADERS)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["active_chats"], 3)
        self.assertEqual(body["transcripts_complete"], 1)
        self.assertEqual(body["transcripts_failed"][0]["chat_id"], -1002)
        self.assertEqual(body["transcripts_failed"][0]["error"], "FloodWait")
        self.assertEqual(body["transcripts_pending"], 1)
        self.assertEqual(body["events"], 3)
        self.assertEqual(body["candidates"], 2)
        self.assertEqual(body["verdicts"], 0)
        self.assertIsNotNone(body["builder_ran_at"])

    def test_candidates_include_excerpt_and_filter_unjudged(self):
        r = self.client.get(
            f"/api/response-audit/candidates?date={DAY}", headers=HEADERS
        )
        self.assertEqual(r.status_code, 200)
        items = r.json()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["excerpt"], [{"id": 1, "text": "hi"}])
        self.assertIsNone(items[0]["verdict"])

        slow_id = items[0]["id"]
        self._post([{"response_event_id": slow_id, "verdict": "BREACH"}])
        items = self.client.get(
            f"/api/response-audit/candidates?date={DAY}", headers=HEADERS
        ).json()
        judged = [i for i in items if i["id"] == slow_id][0]
        self.assertEqual(judged["verdict"]["verdict"], "BREACH")
        unjudged = self.client.get(
            f"/api/response-audit/candidates?date={DAY}&unjudged=true", headers=HEADERS
        ).json()
        self.assertEqual([i["id"] for i in unjudged], [items[1]["id"]])

    def test_bad_date_is_400(self):
        r = self.client.get("/api/response-audit/status?date=nope", headers=HEADERS)
        self.assertEqual(r.status_code, 400)

    # ── verdict upsert ──────────────────────────────────────────────────

    def _post(self, verdicts):
        return self.client.post(
            "/api/response-audit/verdicts", json={"verdicts": verdicts}, headers=HEADERS
        )

    def test_verdict_upsert_is_idempotent(self):
        ev = self.event_ids[0]
        payload = [
            {
                "response_event_id": ev,
                "verdict": "BREACH",
                "reason_code": "slow_reply",
                "summary": "10 min to answer a cashout question",
                "sling_user_id": 42,
                "agent_name": "Alex",
                "judge_version": "j1",
            }
        ]
        first = self._post(payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(
            first.json(),
            {"received": 1, "created": 1, "updated": 0, "missing_event_ids": []},
        )

        second = self._post(payload)
        self.assertEqual(second.json()["created"], 0)
        self.assertEqual(second.json()["updated"], 1)

        payload[0]["verdict"] = "EXCUSED"
        self._post(payload)
        with self.factory() as s:
            rows = s.query(ResponseAuditVerdict).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].verdict, "EXCUSED")
            self.assertEqual(rows[0].agent_name, "Alex")
            self.assertFalse(rows[0].disputed)

    def test_upsert_keeps_disputed_flag(self):
        ev = self.event_ids[0]
        self._post([{"response_event_id": ev, "verdict": "BREACH"}])
        with self.factory() as s:
            row = s.query(ResponseAuditVerdict).one()
            row.disputed = True
            s.commit()
        self._post([{"response_event_id": ev, "verdict": "EXCUSED"}])
        with self.factory() as s:
            self.assertTrue(s.query(ResponseAuditVerdict).one().disputed)

    def test_unknown_event_and_bad_verdict(self):
        r = self._post([{"response_event_id": 999999, "verdict": "BREACH"}])
        self.assertEqual(r.json()["missing_event_ids"], [999999])
        self.assertEqual(r.json()["created"], 0)
        r = self._post([{"response_event_id": self.event_ids[0], "verdict": "MAYBE"}])
        self.assertEqual(r.status_code, 422)

    # ── weekly ──────────────────────────────────────────────────────────

    def test_weekly_requires_monday(self):
        r = self.client.get(
            "/api/response-audit/weekly?week_start=2026-09-29", headers=HEADERS
        )
        self.assertEqual(r.status_code, 400)

    def test_weekly_per_agent(self):
        slow, fast, unanswered = self.event_ids
        self._post(
            [
                {
                    "response_event_id": slow,
                    "verdict": "BREACH",
                    "agent_name": "Alex",
                    "summary": "slow",
                },
                {
                    "response_event_id": unanswered,
                    "verdict": "NOT_A_TRIGGER",
                    "agent_name": "Alex",
                },
            ]
        )
        r = self.client.get(
            "/api/response-audit/weekly?week_start=2026-09-28", headers=HEADERS
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        agents = {a["agent_name"]: a for a in body["agents"]}
        self.assertEqual(agents["Alex"]["triggers"], 1)
        self.assertEqual(agents["Alex"]["median_seconds"], 600)
        self.assertEqual(agents["Alex"]["pct_under_120s"], 0.0)
        breach = agents["Alex"]["breaches"][0]
        self.assertEqual(breach["time_et"], "10:00")
        self.assertEqual(breach["summary"], "slow")
        self.assertEqual(agents["Unattributed"]["triggers"], 1)
        self.assertEqual(body["overall"]["triggers"], 2)
        self.assertEqual(body["overall"]["pct_under_120s"], 50.0)
        self.assertEqual(body["overall"]["p90_seconds"], 600)

    def test_weekly_uses_pacific_week_boundaries(self):
        # Mon 2026-09-28 00:30 PT = 07:30 UTC (in); Sun 2026-09-27 23:30 PT (out).
        with self.factory() as s:
            for i, when in enumerate(
                (_utc(2026, 9, 28, 7, 30), _utc(2026, 9, 28, 6, 30))
            ):
                s.add(
                    ResponseEvent(
                        activity_date=date(2026, 9, 28),
                        chat_id=-1009,
                        club_id=1,
                        start_kind="player_question",
                        clock_start_at=when,
                        clock_start_msg_id=9000 + i,
                        response_seconds=30,
                        is_candidate=False,
                        pre_labels={},
                        rule_version="ra-1",
                    )
                )
            s.commit()
        body = self.client.get(
            "/api/response-audit/weekly?week_start=2026-09-28", headers=HEADERS
        ).json()
        self.assertEqual(body["overall"]["triggers"], 4)


class HeartbeatTests(unittest.IsolatedAsyncioTestCase):
    async def test_alerts_when_candidates_but_no_verdicts(self):
        from bot.services import response_audit_heartbeat as hb

        with (
            patch.object(hb, "candidate_and_verdict_counts", return_value=(3, 0)),
            patch.object(
                hb, "notify_slack_ops", new=AsyncMock(return_value=True)
            ) as slack,
        ):
            sent = await hb.check_response_audit_ran(DAY)
        self.assertTrue(sent)
        slack.assert_awaited_once_with(
            "Response audit for 2026-09-29 has not run.",
            source="response_audit_heartbeat",
        )

    async def test_quiet_when_judged_or_no_candidates(self):
        from bot.services import response_audit_heartbeat as hb

        for counts in ((3, 1), (0, 0)):
            with (
                patch.object(hb, "candidate_and_verdict_counts", return_value=counts),
                patch.object(hb, "notify_slack_ops", new=AsyncMock()) as slack,
            ):
                self.assertFalse(await hb.check_response_audit_ran(DAY))
            slack.assert_not_awaited()

    def test_counts_query(self):
        from bot.services import response_audit_heartbeat as hb

        factory = make_sqlite_session_factory(TABLES)
        with factory() as s:
            s.add(Club(id=1, name="RT", telegram_user_id=1))
            s.flush()
            s.add(
                ResponseEvent(
                    activity_date=DAY,
                    chat_id=-1,
                    club_id=1,
                    start_kind="player_question",
                    clock_start_at=_utc(2026, 9, 29, 14),
                    clock_start_msg_id=1,
                    is_candidate=True,
                    pre_labels={},
                    rule_version="ra-1",
                )
            )
            s.commit()
        with patch("db.connection.get_db", get_db_for(factory)):
            self.assertEqual(hb.candidate_and_verdict_counts(DAY), (1, 0))


if __name__ == "__main__":
    unittest.main()
