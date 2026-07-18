"""Tests for group-chat ticket segmentation + classification."""

from __future__ import annotations

import asyncio
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import create_token, get_current_admin
from api.routes.group_chat_activity import router
from bot.services import group_chat_analysis as analysis
from bot.services import group_chat_analysis_claude as claude
from bot.services.group_chat_analysis_prompts import (
    PROMPT_VERSION,
    SEGMENTATION_SYSTEM,
    TICKET_CATEGORIES,
    build_classification_system,
)
from db.connection import get_db_dependency

TOKEN = create_token()


class PromptSchemaTest(unittest.TestCase):
    def test_categories_closed_set(self):
        self.assertEqual(
            set(TICKET_CATEGORIES),
            {
                "auto_deposit",
                "manual_deposit",
                "unfinished_deposit",
                "cashout",
                "unfinished_cashout",
                "early_rakeback",
                "rakeback",
                "bonus",
                "other",
            },
        )

    def test_prompt_version_bumped_for_quality_rules(self):
        self.assertEqual(PROMPT_VERSION, "2.3.0")

    def test_segmentation_splits_early_rakeback_from_deposit(self):
        self.assertIn("Early rakeback must be its own ticket", SEGMENTATION_SYSTEM)
        self.assertIn("Do **not** fold", SEGMENTATION_SYSTEM)
        self.assertIn("/earlyrb", SEGMENTATION_SYSTEM)

    def test_classification_system_injects_admin_and_bot_roles(self):
        text = build_classification_system(
            admin_names=["RoundTableSupport2"],
            bot_names=["playggsupport", "YTranslateBot"],
        )
        self.assertIn("RoundTableSupport2", text)
        self.assertIn("YTranslateBot", text)
        self.assertIn("admin_first_response", text)
        self.assertIn("never a bot", text.lower())
        self.assertIn("`auto_deposit`", text)
        self.assertIn("`manual_deposit`", text)
        self.assertIn("`unfinished_deposit`", text)
        self.assertIn("`unfinished_cashout`", text)
        self.assertIn("deposit fulfillment line", text)
        self.assertIn("24-hour wait", text)

    def test_validate_classification_rejects_unknown_category(self):
        with self.assertRaises(ValueError):
            claude._validate_classification(
                {
                    "category": "tech_issue",
                    "events": {
                        "customer_first_message": None,
                        "admin_first_response": None,
                        "resolution": None,
                        "escalation": None,
                    },
                    "summary": "x",
                }
            )

    def test_validate_segmentation_requires_message_ids(self):
        with self.assertRaises(ValueError):
            claude._validate_segmentation({"tickets": [{"ticket_index": 0}]})


class RoleListsTest(unittest.TestCase):
    def test_role_lists_include_staff_and_bots(self):
        cfg = SimpleNamespace(
            bot_account="@playggsupport",
            users_to_add=("@RoundTableSupport2",),
            club_key="round_table",
        )
        with (
            patch.object(
                analysis,
                "get_club_gc_config_by_link_club_id",
                return_value=cfg,
            ),
            patch.object(
                analysis,
                "get_gc_users_to_add",
                return_value=("@RoundTableSupport2", "@RoundTableSupport3"),
            ),
        ):
            admins, bots = analysis.role_lists_for_club(2)
        self.assertTrue(any("RoundTableSupport2" in a for a in admins))
        self.assertTrue(any("playggsupport" in b.lower() for b in bots))
        self.assertTrue(any("YTranslateBot" in b for b in bots))


class AnalyzeTranscriptTest(unittest.IsolatedAsyncioTestCase):
    async def test_success_replaces_tickets_and_marks_complete(self):
        messages = [
            {"id": 1, "text": "deposit 50", "is_bot": False},
            {"id": 2, "text": "Added $50", "is_bot": False},
        ]
        with (
            patch.object(analysis, "_mark_analysis_attempt_start"),
            patch.object(
                analysis,
                "_load_transcript_messages",
                return_value=(2, messages),
            ),
            patch.object(analysis, "chat_display_name", return_value="RT / player"),
            patch.object(
                analysis,
                "role_lists_for_club",
                return_value=(["RoundTableSupport2"], ["playggsupport"]),
            ),
            patch.object(
                analysis,
                "segment_messages",
                new_callable=AsyncMock,
                return_value={
                    "tickets": [
                        {
                            "ticket_index": 0,
                            "start_msg_id": 1,
                            "end_msg_id": 2,
                            "message_ids": [1, 2],
                            "brief_summary": "deposit",
                        }
                    ]
                },
            ),
            patch.object(
                analysis,
                "classify_ticket",
                new_callable=AsyncMock,
                return_value={
                    "category": "manual_deposit",
                    "events": {
                        "customer_first_message": "2026-07-17T12:00:00+00:00",
                        "admin_first_response": "2026-07-17T12:01:00+00:00",
                        "resolution": "2026-07-17T12:02:00+00:00",
                        "escalation": None,
                    },
                    "summary": "AM added chips",
                },
            ),
            patch.object(analysis, "_replace_tickets", return_value=1) as replace,
            patch.object(analysis, "_mark_analysis_complete") as mark_ok,
            patch.object(analysis, "_mark_analysis_failed") as mark_fail,
            patch.object(analysis, "get_anthropic_model", return_value="claude-sonnet-4-5"),
        ):
            result = await analysis.analyze_transcript_for_chat(
                activity_date=date(2026, 7, 17),
                chat_id=-1001,
                club_id=2,
            )

        self.assertEqual(result.status, analysis.ANALYSIS_COMPLETE)
        self.assertEqual(result.ticket_count, 1)
        replace.assert_called_once()
        mark_ok.assert_called_once()
        mark_fail.assert_not_called()

    async def test_failure_does_not_replace_tickets(self):
        with (
            patch.object(analysis, "_mark_analysis_attempt_start"),
            patch.object(
                analysis,
                "_load_transcript_messages",
                return_value=(2, [{"id": 1, "text": "hi"}]),
            ),
            patch.object(analysis, "chat_display_name", return_value="chat"),
            patch.object(
                analysis,
                "role_lists_for_club",
                return_value=([], []),
            ),
            patch.object(
                analysis,
                "segment_messages",
                new_callable=AsyncMock,
                side_effect=RuntimeError("claude down"),
            ),
            patch.object(analysis, "_replace_tickets") as replace,
            patch.object(analysis, "_mark_analysis_complete") as mark_ok,
            patch.object(analysis, "_mark_analysis_failed") as mark_fail,
        ):
            result = await analysis.analyze_transcript_for_chat(
                activity_date=date(2026, 7, 17),
                chat_id=-1001,
            )

        self.assertEqual(result.status, analysis.ANALYSIS_FAILED)
        replace.assert_not_called()
        mark_ok.assert_not_called()
        mark_fail.assert_called_once()


class ListTargetsForceTest(unittest.TestCase):
    def test_force_includes_complete(self):
        complete_row = SimpleNamespace(
            activity_date=date(2026, 7, 17),
            chat_id=-1,
            club_id=2,
            status="complete",
            analysis_status="complete",
        )
        session = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.all.return_value = [complete_row]
        session.query.return_value = q

        with patch.object(analysis, "get_db") as get_db:
            ctx = MagicMock()
            ctx.__enter__.return_value = session
            ctx.__exit__.return_value = None
            get_db.return_value = ctx
            targets = analysis.list_analysis_targets(
                date(2026, 7, 17), force=True
            )

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].chat_id, -1)


class AnalyzeRetriesTest(unittest.IsolatedAsyncioTestCase):
    async def test_skips_already_complete_and_retries_failed(self):
        targets = [
            analysis.AnalysisChatTarget(
                activity_date=date(2026, 7, 17),
                chat_id=-1,
                club_id=2,
            ),
            analysis.AnalysisChatTarget(
                activity_date=date(2026, 7, 17),
                chat_id=-2,
                club_id=2,
            ),
        ]
        call_count = {"n": 0}

        async def _analyze(**kwargs):
            call_count["n"] += 1
            chat_id = kwargs["chat_id"]
            if chat_id == -1:
                return analysis.AnalysisChatResult(
                    chat_id=-1,
                    club_id=2,
                    status=analysis.ANALYSIS_COMPLETE,
                    ticket_count=1,
                )
            # First attempt fails, second succeeds
            if call_count["n"] <= 2:
                return analysis.AnalysisChatResult(
                    chat_id=-2,
                    club_id=2,
                    status=analysis.ANALYSIS_FAILED,
                    error="boom",
                )
            return analysis.AnalysisChatResult(
                chat_id=-2,
                club_id=2,
                status=analysis.ANALYSIS_COMPLETE,
                ticket_count=1,
            )

        list_calls = {"n": 0}

        def _list_targets(*_a, **_k):
            list_calls["n"] += 1
            if list_calls["n"] == 1:
                return list(targets)
            # After first pass, only -2 still open
            return [
                analysis.AnalysisChatTarget(
                    activity_date=date(2026, 7, 17),
                    chat_id=-2,
                    club_id=2,
                )
            ]

        with (
            patch.object(analysis, "list_analysis_targets", side_effect=_list_targets),
            patch.object(
                analysis,
                "analyze_transcript_for_chat",
                new_callable=AsyncMock,
                side_effect=_analyze,
            ),
        ):
            summary = await analysis.analyze_with_retries(
                date(2026, 7, 17),
                budget_seconds=60,
                concurrency=0,
            )

        self.assertEqual(summary.complete, 2)
        self.assertEqual(summary.failed, 0)

    async def test_parallel_runs_multiple_chats(self):
        started = asyncio.Event()
        release = asyncio.Event()
        in_flight = {"n": 0, "max": 0}

        async def _analyze(**kwargs):
            in_flight["n"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["n"])
            started.set()
            await release.wait()
            in_flight["n"] -= 1
            return analysis.AnalysisChatResult(
                chat_id=kwargs["chat_id"],
                club_id=2,
                status=analysis.ANALYSIS_COMPLETE,
                ticket_count=1,
            )

        targets = [
            analysis.AnalysisChatTarget(
                activity_date=date(2026, 7, 17),
                chat_id=-i,
                club_id=2,
            )
            for i in range(1, 4)
        ]
        with (
            patch.object(analysis, "list_analysis_targets", return_value=targets),
            patch.object(
                analysis,
                "analyze_transcript_for_chat",
                new_callable=AsyncMock,
                side_effect=_analyze,
            ),
        ):
            task = asyncio.create_task(
                analysis.analyze_with_retries(
                    date(2026, 7, 17),
                    budget_seconds=60,
                    concurrency=0,
                )
            )
            await started.wait()
            # Give gather a tick to schedule siblings
            await asyncio.sleep(0)
            self.assertGreaterEqual(in_flight["max"], 2)
            release.set()
            summary = await task

        self.assertEqual(summary.complete, 3)
        self.assertGreaterEqual(in_flight["max"], 2)


class DurationHelperTest(unittest.TestCase):
    def test_resolution_path(self):
        from api.group_chat_ticket_helpers import compute_ticket_duration

        seconds, source = compute_ticket_duration(
            {
                "customer_first_message": "2026-07-17T14:00:00+00:00",
                "resolution": "2026-07-17T14:05:00+00:00",
            },
            [1, 2],
            None,
        )
        self.assertEqual(seconds, 300)
        self.assertEqual(source, "resolution")

    def test_message_span_fallback(self):
        from api.group_chat_ticket_helpers import compute_ticket_duration

        seconds, source = compute_ticket_duration(
            {"customer_first_message": "2026-07-17T14:00:00+00:00"},
            [10, 11],
            {
                10: {"id": 10, "date": "2026-07-17T14:00:00+00:00"},
                11: {"id": 11, "date": "2026-07-17T14:02:30+00:00"},
            },
        )
        self.assertEqual(seconds, 150)
        self.assertEqual(source, "message_span")

    def test_null_when_neither(self):
        from api.group_chat_ticket_helpers import compute_ticket_duration

        seconds, source = compute_ticket_duration(None, [1], {})
        self.assertIsNone(seconds)
        self.assertIsNone(source)


class RoleAssignTest(unittest.TestCase):
    def test_assign_roles(self):
        from api.group_chat_ticket_helpers import assign_message_role

        self.assertEqual(
            assign_message_role(
                {"is_bot": True, "username": "x"},
                admin_names=[],
                bot_names=[],
            ),
            "bot",
        )
        self.assertEqual(
            assign_message_role(
                {"is_bot": False, "username": "StaffBot"},
                admin_names=["agent1"],
                bot_names=["StaffBot"],
            ),
            "bot",
        )
        self.assertEqual(
            assign_message_role(
                {"is_bot": False, "username": "agent1"},
                admin_names=["agent1"],
                bot_names=[],
            ),
            "admin",
        )
        self.assertEqual(
            assign_message_role(
                {"is_bot": False, "username": "player"},
                admin_names=["agent1"],
                bot_names=[],
            ),
            "customer",
        )

    def test_display_name_matches_staff_username(self):
        from api.group_chat_ticket_helpers import assign_message_role

        self.assertEqual(
            assign_message_role(
                {
                    "is_bot": False,
                    "username": None,
                    "sender_name": "Creator Club Support",
                },
                admin_names=["CreatorClubSupport2", "CreatorClubSupport3"],
                bot_names=[],
            ),
            "admin",
        )

    def test_support_in_display_name_is_admin(self):
        from api.group_chat_ticket_helpers import assign_message_role

        self.assertEqual(
            assign_message_role(
                {
                    "is_bot": False,
                    "username": None,
                    "sender_name": "Creator Club Support",
                },
                admin_names=[],
                bot_names=[],
            ),
            "admin",
        )

    def test_group_title_sender_is_customer(self):
        from api.group_chat_ticket_helpers import assign_message_role

        self.assertEqual(
            assign_message_role(
                {
                    "is_bot": False,
                    "username": None,
                    "sender_name": "CC / 9446-6280 / Batuhan",
                },
                admin_names=["CreatorClubSupport2"],
                bot_names=[],
                group_name="CC / 9446-6280 / Batuhan",
            ),
            "customer",
        )


class TicketRestTest(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(router)

        async def _admin():
            return {"sub": "admin"}

        self.app.dependency_overrides[get_current_admin] = _admin
        self.db = MagicMock()
        self.app.dependency_overrides[get_db_dependency] = lambda: self.db
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()

    def _ticket_row(self, **overrides):
        base = dict(
            id=1,
            activity_date=date(2026, 7, 17),
            chat_id=-1001,
            club_id=2,
            ticket_index=0,
            start_msg_id=1,
            end_msg_id=2,
            message_ids=[1, 2],
            brief_summary="deposit",
            category="manual_deposit",
            events={
                "customer_first_message": "2026-07-17T14:00:00+00:00",
                "resolution": "2026-07-17T14:05:00+00:00",
            },
            summary="done",
            prompt_version="2.3.0",
            model="claude-sonnet-4-5",
            created_at=None,
            updated_at=None,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def _mock_enrich_queries(self, ticket_rows):
        """db.query(...) chain: tickets → clubs → groups → transcripts."""

        ticket_q = MagicMock()
        ticket_q.filter.return_value = ticket_q
        ticket_q.order_by.return_value = ticket_q
        ticket_q.all.return_value = ticket_rows

        club_q = MagicMock()
        club_q.filter.return_value = club_q
        club_q.all.return_value = [SimpleNamespace(id=2, name="Test Club")]

        group_q = MagicMock()
        group_q.filter.return_value = group_q
        group_q.all.return_value = [
            SimpleNamespace(chat_id=-1001, name="Player Group")
        ]

        transcript_q = MagicMock()
        transcript_q.filter.return_value = transcript_q
        transcript_q.all.return_value = []
        transcript_q.one_or_none.return_value = None

        def _query(model):
            name = getattr(model, "__name__", str(model))
            if name == "GroupChatTicket":
                return ticket_q
            if name == "Club":
                return club_q
            if name == "Group":
                return group_q
            if name == "GroupChatDailyTranscript":
                return transcript_q
            return MagicMock()

        self.db.query.side_effect = _query
        return ticket_q, transcript_q

    def test_list_tickets_enriched(self):
        row = self._ticket_row()
        self._mock_enrich_queries([row])

        resp = self.client.get(
            "/api/group-chat-tickets",
            params={"activity_date": "2026-07-17"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["category"], "manual_deposit")
        self.assertEqual(body[0]["club_name"], "Test Club")
        self.assertEqual(body[0]["group_name"], "Player Group")
        self.assertEqual(body[0]["duration_seconds"], 300)
        self.assertEqual(body[0]["duration_source"], "resolution")
        self.assertEqual(
            body[0]["customer_first_message"], "2026-07-17T14:00:00+00:00"
        )

    def test_list_tickets_for_chat(self):
        self._mock_enrich_queries([])

        resp = self.client.get(
            "/api/group-chat-tickets/-1001",
            params={"activity_date": "2026-07-17"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_ticket_messages_endpoint(self):
        ticket = self._ticket_row(
            message_ids=[101, 102],
            events={},
        )
        transcript = SimpleNamespace(
            activity_date=date(2026, 7, 17),
            chat_id=-1001,
            messages=[
                {
                    "id": 101,
                    "date": "2026-07-17T14:00:00+00:00",
                    "sender_id": 9,
                    "sender_name": "Player",
                    "username": "player1",
                    "is_bot": False,
                    "text": "hi",
                    "media_type": None,
                    "media_filename": None,
                },
                {
                    "id": 102,
                    "date": "2026-07-17T14:01:00+00:00",
                    "sender_id": 1,
                    "sender_name": "Agent",
                    "username": "agent1",
                    "is_bot": False,
                    "text": "hello",
                    "media_type": None,
                    "media_filename": None,
                },
                {
                    "id": 999,
                    "date": "2026-07-17T14:02:00+00:00",
                    "sender_id": 2,
                    "sender_name": "Other",
                    "username": "other",
                    "is_bot": False,
                    "text": "skip me",
                    "media_type": None,
                    "media_filename": None,
                },
            ],
        )

        ticket_q = MagicMock()
        ticket_q.filter.return_value = ticket_q
        ticket_q.one_or_none.return_value = ticket

        transcript_q = MagicMock()
        transcript_q.filter.return_value = transcript_q
        transcript_q.one_or_none.return_value = transcript

        group_q = MagicMock()
        group_q.filter.return_value = group_q
        group_q.one_or_none.return_value = SimpleNamespace(
            chat_id=-1001, name="Player Group"
        )

        def _query(model):
            name = getattr(model, "__name__", str(model))
            if name == "GroupChatTicket":
                return ticket_q
            if name == "GroupChatDailyTranscript":
                return transcript_q
            if name == "Group":
                return group_q
            return MagicMock()

        self.db.query.side_effect = _query

        with patch(
            "api.group_chat_ticket_helpers.role_lists_for_club",
            return_value=(["agent1"], ["YTranslateBot"]),
        ):
            resp = self.client.get(
                "/api/group-chat-tickets/by-id/1/messages",
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["ticket_id"], 1)
        self.assertEqual(len(body["messages"]), 2)
        self.assertEqual(body["messages"][0]["role"], "customer")
        self.assertEqual(body["messages"][1]["role"], "admin")
        self.assertEqual(body["messages"][0]["text"], "hi")

    def test_by_id_does_not_break_chat_id_route(self):
        self._mock_enrich_queries([])
        resp = self.client.get(
            "/api/group-chat-tickets/-1001",
            params={"activity_date": "2026-07-17"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
