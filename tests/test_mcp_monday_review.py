"""MCP Monday Review tools: board/tasks/history reads, confirm-gated writes."""

from __future__ import annotations

import os
import unittest
from datetime import date
from unittest.mock import patch

from mcp.server.mcpserver.exceptions import ToolError

import api.monday_review as svc
import mcp_server.db as mcp_db
from mcp_server.tools.monday_review import (
    add_monday_review_note,
    complete_monday_review_task,
    create_monday_review_task,
    get_monday_review_board,
    list_monday_review_history,
    list_monday_review_tasks,
)
from tests.support.sqlite_db import make_sqlite_session_factory

TABLES = [
    "monday_review_tasks",
    "monday_review_task_notes",
    "monday_review_completions",
]
TODAY = date(2026, 10, 14)
THIS_MONDAY = "2026-10-12"
NEXT_MONDAY = "2026-10-19"
NOW = svc.datetime(2026, 10, 14, 15, 0, tzinfo=svc.timezone.utc)


class MondayReviewToolTests(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(
            os.environ, {"MCP_API_TOKENS": "dev:" + "x" * 32, "MCP_ACTOR": "tester"}
        )
        env.start()
        self.addCleanup(env.stop)
        clock = patch.object(svc, "today_et", lambda: TODAY)
        clock.start()
        self.addCleanup(clock.stop)
        now = patch.object(svc, "_now", lambda: NOW)
        now.start()
        self.addCleanup(now.stop)
        self.factory = make_sqlite_session_factory(TABLES)
        mcp_db.session_factory = self.factory
        self.addCleanup(setattr, mcp_db, "session_factory", None)
        with self.factory() as session:
            svc.create_task(
                session,
                title="Review weekly P&L",
                links=[{"label": "Sheet", "url": "https://docs.google.com/x"}],
                assignees=["Aiden"],
            )
            svc.create_task(session, title="Pay affiliates", assignees=["Edaad"])
            session.commit()

    def test_board_and_tasks_read(self) -> None:
        board = get_monday_review_board()
        self.assertEqual(board["item"]["week_of"], THIS_MONDAY)
        self.assertEqual(board["item"]["total"], 2)
        self.assertEqual(board["item"]["done"], 0)
        first = board["item"]["tasks"][0]
        self.assertEqual(first["title"], "Review weekly P&L")
        self.assertEqual(
            first["links"], [{"label": "Sheet", "url": "https://docs.google.com/x"}]
        )
        self.assertEqual(first["assignees"], ["Aiden"])
        self.assertFalse(first["done"])

        tasks = list_monday_review_tasks()
        self.assertEqual(tasks["total"], 2)
        self.assertEqual(
            [t["title"] for t in tasks["items"]],
            ["Review weekly P&L", "Pay affiliates"],
        )

        with self.assertRaises(ToolError):
            get_monday_review_board(week_of="2026-10-13")

    def test_complete_requires_confirm_and_resolves_title(self) -> None:
        result = complete_monday_review_task(
            "affiliates", completed_by="edaad", notes="done"
        )
        self.assertFalse(result["applied"])
        self.assertTrue(result["refused"])
        self.assertEqual(result["proposed"]["week_of"], THIS_MONDAY)
        self.assertEqual(result["proposed"]["completed_by"], "Edaad")
        self.assertEqual(get_monday_review_board()["item"]["done"], 0)

        result = complete_monday_review_task(
            "affiliates", completed_by="edaad", notes="done", confirm=True
        )
        self.assertTrue(result["applied"])
        self.assertEqual(result["actor"], "tester")
        self.assertIsNone(result["before"])
        self.assertEqual(result["after"]["completed_by"], "Edaad")
        board = get_monday_review_board()["item"]
        self.assertEqual(board["done"], 1)
        done = [t for t in board["tasks"] if t["done"]]
        self.assertEqual(done[0]["title"], "Pay affiliates")

        # Second call updates the existing completion.
        result = complete_monday_review_task(
            "affiliates", notes="revised", confirm=True
        )
        self.assertTrue(result["applied"])
        self.assertEqual(result["before"]["notes"], "done")
        self.assertEqual(result["after"]["notes"], "revised")
        self.assertEqual(result["after"]["completed_by"], "Edaad")

        with self.assertRaises(ToolError):
            complete_monday_review_task("affiliates", week_of=NEXT_MONDAY, confirm=True)
        with self.assertRaises(ToolError):
            complete_monday_review_task("does not exist", confirm=True)
        with self.assertRaises(ToolError):
            complete_monday_review_task("e", confirm=True)  # ambiguous

    def test_add_note_defaults_to_next_monday(self) -> None:
        result = add_monday_review_note("Review weekly P&L", "Ask about GTO")
        self.assertFalse(result["applied"])
        self.assertEqual(result["proposed"]["kind"], "temporary")
        self.assertEqual(result["proposed"]["week_of"], NEXT_MONDAY)

        result = add_monday_review_note(
            "Review weekly P&L", "Ask about GTO", confirm=True
        )
        self.assertTrue(result["applied"])
        result = add_monday_review_note(
            "1", "Export first", kind="persistent", confirm=True
        )
        self.assertTrue(result["applied"])
        self.assertIsNone(result["after"]["week_of"])

        task = get_monday_review_board()["item"]["tasks"][0]
        self.assertEqual(
            [n["body"] for n in task["persistent_notes"]], ["Export first"]
        )
        self.assertEqual(task["week_notes"], [])
        self.assertEqual([n["body"] for n in task["upcoming_notes"]], ["Ask about GTO"])
        next_task = get_monday_review_board(week_of=NEXT_MONDAY)["item"]["tasks"][0]
        self.assertEqual(
            [n["body"] for n in next_task["week_notes"]], ["Ask about GTO"]
        )

        with self.assertRaises(ToolError):
            add_monday_review_note("1", "x", kind="forever", confirm=True)
        with self.assertRaises(ToolError):
            add_monday_review_note("1", "   ", confirm=True)

    def test_create_task_and_history(self) -> None:
        result = create_monday_review_task(
            "Check bonuses",
            links=[{"label": "", "url": "clubgg.com"}],
            assignees=["Sam", "aiden"],
        )
        self.assertFalse(result["applied"])
        self.assertEqual(
            result["proposed"]["links"],
            [{"label": "clubgg.com", "url": "https://clubgg.com"}],
        )
        self.assertEqual(result["proposed"]["assignees"], ["Sam", "Aiden"])
        result = create_monday_review_task(
            "Check bonuses", assignees=["Sam"], confirm=True
        )
        self.assertTrue(result["applied"])
        self.assertEqual(list_monday_review_tasks()["total"], 3)
        with self.assertRaises(ToolError):
            create_monday_review_task(
                "x", links=[{"label": "bad", "url": "javascript:x"}]
            )

        complete_monday_review_task("1", week_of="2026-10-05", confirm=True)
        with self.factory() as session:
            for row in session.query(svc.MondayReviewTask).all():
                row.created_at = svc.datetime(2026, 9, 28, 12, tzinfo=svc.timezone.utc)
            session.commit()
        history = list_monday_review_history(limit=1)["item"]
        self.assertTrue(history["has_more"])
        week = history["weeks"][0]
        self.assertEqual(week["week_of"], "2026-10-05")
        self.assertEqual(week["done"], 1)
        self.assertEqual(week["missed"], 2)


if __name__ == "__main__":
    unittest.main()
