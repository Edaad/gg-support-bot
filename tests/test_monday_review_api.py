"""Monday Review API: tasks, links, assignees, notes, completions, history, RBAC."""

from __future__ import annotations

import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.monday_review as svc
from api.auth import ROLE_ACCOUNT_MANAGER, ROLE_ADMIN, create_token
from api.routes.monday_review import router
from db.connection import get_db_dependency
from tests.support.sqlite_db import make_sqlite_session_factory

TABLES = [
    "monday_review_tasks",
    "monday_review_task_notes",
    "monday_review_completions",
]

# Fixed clock: Wednesday 2026-10-14 ET -> current week Mon 2026-10-12.
TODAY = date(2026, 10, 14)
THIS_MONDAY = date(2026, 10, 12)
NEXT_MONDAY = date(2026, 10, 19)
LAST_MONDAY = date(2026, 10, 5)
NOW = svc.datetime(2026, 10, 14, 15, 0, tzinfo=svc.timezone.utc)


def _make_app(factory) -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    def override_db():
        session = factory()
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


class MondayReviewApiTests(unittest.TestCase):
    def setUp(self) -> None:
        import api.auth as auth_mod

        auth_mod._SECRET = None
        env = patch.dict(
            os.environ,
            {"DASHBOARD_PASSWORD": "admin-secret", "DASHBOARD_AM_PASSWORD": "am"},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(setattr, auth_mod, "_SECRET", None)
        clock = patch.object(svc, "today_et", lambda: TODAY)
        clock.start()
        self.addCleanup(clock.stop)
        now = patch.object(svc, "_now", lambda: NOW)
        now.start()
        self.addCleanup(now.stop)
        self.factory = make_sqlite_session_factory(TABLES)
        self.client = TestClient(_make_app(self.factory))
        self.admin = {"Authorization": f"Bearer {create_token(ROLE_ADMIN)}"}
        self.am = {"Authorization": f"Bearer {create_token(ROLE_ACCOUNT_MANAGER)}"}

    # helpers ---------------------------------------------------------------

    def _create(self, **overrides):
        payload = {
            "title": "Review weekly P&L",
            "description": "Check every club's net.",
            "links": [
                {"label": "Sheet", "url": "https://docs.google.com/x"},
                {"label": "", "url": "clubgg.com/reports"},
            ],
            "assignees": ["aiden", "Other person", "Aiden"],
        }
        payload.update(overrides)
        res = self.client.post(
            "/api/monday-review/tasks", json=payload, headers=self.admin
        )
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()

    def _board(self, week_of: date | None = None):
        params = {"week_of": week_of.isoformat()} if week_of else {}
        res = self.client.get(
            "/api/monday-review/board", params=params, headers=self.admin
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    # tests -----------------------------------------------------------------

    def test_non_admin_roles_are_rejected(self) -> None:
        for method, path in (
            ("get", "/api/monday-review/board"),
            ("get", "/api/monday-review/tasks"),
            ("post", "/api/monday-review/tasks"),
            ("get", "/api/monday-review/history"),
        ):
            with self.subTest(path=path):
                kwargs = {"json": {}} if method == "post" else {}
                res = getattr(self.client, method)(path, headers=self.am, **kwargs)
                self.assertEqual(res.status_code, 403)
        self.assertEqual(self.client.get("/api/monday-review/board").status_code, 403)

    def test_meta_exposes_presets_and_week_anchors(self) -> None:
        res = self.client.get("/api/monday-review/meta", headers=self.admin)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["assignee_presets"], ["Aiden", "Edaad"])
        self.assertEqual(body["current_week_of"], THIS_MONDAY.isoformat())
        self.assertEqual(body["next_week_of"], NEXT_MONDAY.isoformat())

    def test_create_normalizes_links_and_assignees(self) -> None:
        task = self._create()
        self.assertEqual(task["title"], "Review weekly P&L")
        self.assertEqual(
            task["links"],
            [
                {"label": "Sheet", "url": "https://docs.google.com/x"},
                {"label": "clubgg.com", "url": "https://clubgg.com/reports"},
            ],
        )
        self.assertEqual(task["assignees"], ["Aiden", "Other person"])
        self.assertFalse(task["archived"])
        second = self._create(title="Second task", links=[], assignees=[])
        self.assertEqual(second["sort_order"], task["sort_order"] + 1)

    def test_create_rejects_bad_input(self) -> None:
        for payload, needle in (
            ({"title": "   "}, "title is required"),
            (
                {
                    "title": "x",
                    "links": [{"label": "bad", "url": "javascript:alert(1)"}],
                },
                "http",
            ),
            ({"title": "x", "links": [{"label": "bad", "url": "ftp://files"}]}, "http"),
            ({"title": "x", "assignees": ["a" * 65]}, "assignee name"),
            ({"title": "x" * 201}, "title must be"),
        ):
            with self.subTest(payload=payload):
                res = self.client.post(
                    "/api/monday-review/tasks", json=payload, headers=self.admin
                )
                self.assertEqual(res.status_code, 400, res.text)
                self.assertIn(needle, res.json()["detail"])

    def test_board_defaults_to_current_week_and_tracks_completion(self) -> None:
        task = self._create()
        board = self._board()
        self.assertEqual(board["week_of"], THIS_MONDAY.isoformat())
        self.assertTrue(board["is_current_week"])
        self.assertEqual(board["total"], 1)
        self.assertEqual(board["done"], 0)
        self.assertFalse(board["all_done"])
        self.assertFalse(board["tasks"][0]["done"])
        self.assertIsNone(board["tasks"][0]["completion"])

        res = self.client.post(
            f"/api/monday-review/tasks/{task['id']}/complete",
            json={"completed_by": "edaad", "notes": "All clubs positive"},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 200, res.text)
        completion = res.json()
        self.assertEqual(completion["week_of"], THIS_MONDAY.isoformat())
        self.assertEqual(completion["completed_by"], "Edaad")
        self.assertEqual(completion["notes"], "All clubs positive")

        board = self._board()
        self.assertEqual(board["done"], 1)
        self.assertTrue(board["all_done"])
        self.assertTrue(board["tasks"][0]["done"])
        self.assertEqual(board["tasks"][0]["completion"]["id"], completion["id"])

        # Re-completing updates notes in place and keeps the same row.
        res = self.client.post(
            f"/api/monday-review/tasks/{task['id']}/complete",
            json={"notes": "Edited"},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["id"], completion["id"])
        self.assertEqual(res.json()["notes"], "Edited")
        self.assertEqual(res.json()["completed_by"], "Edaad")

        # Uncheck.
        res = self.client.delete(
            f"/api/monday-review/tasks/{task['id']}/complete", headers=self.admin
        )
        self.assertEqual(res.status_code, 204)
        self.assertEqual(self._board()["done"], 0)
        res = self.client.delete(
            f"/api/monday-review/tasks/{task['id']}/complete", headers=self.admin
        )
        self.assertEqual(res.status_code, 404)

    def test_week_of_must_be_a_monday_and_not_in_the_future(self) -> None:
        task = self._create()
        res = self.client.get(
            "/api/monday-review/board",
            params={"week_of": "2026-10-14"},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Monday", res.json()["detail"])
        res = self.client.post(
            f"/api/monday-review/tasks/{task['id']}/complete",
            json={"week_of": NEXT_MONDAY.isoformat()},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("future", res.json()["detail"])
        # Completing a past week (catching up) is allowed.
        res = self.client.post(
            f"/api/monday-review/tasks/{task['id']}/complete",
            json={"week_of": LAST_MONDAY.isoformat()},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 200)

    def test_persistent_and_temporary_notes(self) -> None:
        task = self._create()
        res = self.client.post(
            f"/api/monday-review/tasks/{task['id']}/notes",
            json={"kind": "persistent", "body": "Always export before 10am"},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 201, res.text)
        persistent = res.json()
        self.assertIsNone(persistent["week_of"])

        # Temporary defaults to next Monday (the following week's review).
        res = self.client.post(
            f"/api/monday-review/tasks/{task['id']}/notes",
            json={"kind": "temporary", "body": "Ask Vaughn about GTO"},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 201, res.text)
        temp = res.json()
        self.assertEqual(temp["week_of"], NEXT_MONDAY.isoformat())

        res = self.client.post(
            f"/api/monday-review/tasks/{task['id']}/notes",
            json={
                "kind": "temporary",
                "body": "This week only",
                "week_of": THIS_MONDAY.isoformat(),
            },
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 201, res.text)

        this_week = self._board()["tasks"][0]
        self.assertEqual(
            [n["body"] for n in this_week["persistent_notes"]],
            ["Always export before 10am"],
        )
        self.assertEqual(
            [n["body"] for n in this_week["week_notes"]], ["This week only"]
        )
        self.assertEqual(
            [n["body"] for n in this_week["upcoming_notes"]], ["Ask Vaughn about GTO"]
        )

        next_week = self._board(NEXT_MONDAY)["tasks"][0]
        self.assertEqual(
            [n["body"] for n in next_week["persistent_notes"]],
            ["Always export before 10am"],
        )
        self.assertEqual(
            [n["body"] for n in next_week["week_notes"]], ["Ask Vaughn about GTO"]
        )
        self.assertEqual(next_week["upcoming_notes"], [])

        # Convert the temporary note to persistent and back; edit body.
        res = self.client.patch(
            f"/api/monday-review/notes/{temp['id']}",
            json={"kind": "persistent", "body": "Now always"},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIsNone(res.json()["week_of"])
        self.assertEqual(res.json()["body"], "Now always")
        res = self.client.patch(
            f"/api/monday-review/notes/{temp['id']}",
            json={"kind": "temporary", "week_of": "2026-10-20"},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 400)
        res = self.client.delete(
            f"/api/monday-review/notes/{temp['id']}", headers=self.admin
        )
        self.assertEqual(res.status_code, 204)
        res = self.client.delete(
            f"/api/monday-review/notes/{temp['id']}", headers=self.admin
        )
        self.assertEqual(res.status_code, 404)
        res = self.client.post(
            f"/api/monday-review/tasks/{task['id']}/notes",
            json={"kind": "temporary", "body": "   "},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 400)

    def test_archive_hides_from_board_but_keeps_history(self) -> None:
        task = self._create()
        other = self._create(title="Keep me", links=[], assignees=["Edaad"])
        self.client.post(
            f"/api/monday-review/tasks/{task['id']}/complete",
            json={"week_of": LAST_MONDAY.isoformat(), "completed_by": "Aiden"},
            headers=self.admin,
        )
        res = self.client.patch(
            f"/api/monday-review/tasks/{task['id']}",
            json={"archived": True},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["archived"])

        # Archived this week: still shown this week (archive week included), gone next week.
        self.assertEqual(
            {t["id"] for t in self._board()["tasks"]}, {task["id"], other["id"]}
        )
        self.assertEqual(
            [t["id"] for t in self._board(NEXT_MONDAY)["tasks"]], [other["id"]]
        )

        active = self.client.get("/api/monday-review/tasks", headers=self.admin).json()
        self.assertEqual([t["id"] for t in active], [other["id"]])
        everything = self.client.get(
            "/api/monday-review/tasks",
            params={"include_archived": "true"},
            headers=self.admin,
        ).json()
        self.assertEqual([t["id"] for t in everything], [other["id"], task["id"]])

        history = self.client.get(
            "/api/monday-review/history", headers=self.admin
        ).json()
        self.assertFalse(history["has_more"])
        self.assertEqual(len(history["weeks"]), 1)
        week = history["weeks"][0]
        self.assertEqual(week["week_of"], LAST_MONDAY.isoformat())
        by_id = {t["task_id"]: t for t in week["tasks"]}
        self.assertEqual(by_id[task["id"]]["status"], "done")
        self.assertEqual(by_id[task["id"]]["completion"]["completed_by"], "Aiden")
        self.assertTrue(by_id[task["id"]]["archived"])
        self.assertNotIn(other["id"], by_id)

        res = self.client.patch(
            f"/api/monday-review/tasks/{task['id']}",
            json={"archived": False, "title": "Restored"},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["archived"])
        self.assertEqual(res.json()["title"], "Restored")

    def test_history_marks_missed_weeks_and_pages(self) -> None:
        task = self._create()
        with self.factory() as session:
            row = session.get(svc.MondayReviewTask, task["id"])
            row.created_at = svc.datetime(2026, 9, 1, 12, tzinfo=svc.timezone.utc)
            session.commit()
        self.client.post(
            f"/api/monday-review/tasks/{task['id']}/complete",
            json={"week_of": LAST_MONDAY.isoformat(), "completed_by": "Edaad"},
            headers=self.admin,
        )
        res = self.client.get(
            "/api/monday-review/history", params={"limit": 2}, headers=self.admin
        )
        self.assertEqual(res.status_code, 200, res.text)
        history = res.json()
        self.assertEqual(
            [w["week_of"] for w in history["weeks"]],
            [LAST_MONDAY.isoformat(), (LAST_MONDAY - timedelta(days=7)).isoformat()],
        )
        self.assertEqual(history["weeks"][0]["done"], 1)
        self.assertEqual(history["weeks"][1]["missed"], 1)
        self.assertEqual(history["weeks"][1]["tasks"][0]["status"], "missed")
        self.assertTrue(history["has_more"])
        self.assertEqual(history["next_before"], history["weeks"][-1]["week_of"])

        res = self.client.get(
            "/api/monday-review/history",
            params={"limit": 52, "before": history["next_before"]},
            headers=self.admin,
        )
        older = res.json()
        self.assertFalse(older["has_more"])
        self.assertEqual(older["weeks"][-1]["week_of"], "2026-08-31")

    def test_reorder_and_delete(self) -> None:
        a = self._create(title="A", links=[], assignees=[])
        b = self._create(title="B", links=[], assignees=[])
        c = self._create(title="C", links=[], assignees=[])
        res = self.client.post(
            "/api/monday-review/tasks/reorder",
            json={"ordered_ids": [c["id"], a["id"]]},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual([t["title"] for t in res.json()], ["C", "A", "B"])
        self.assertEqual([t["title"] for t in self._board()["tasks"]], ["C", "A", "B"])
        res = self.client.post(
            "/api/monday-review/tasks/reorder",
            json={"ordered_ids": [999]},
            headers=self.admin,
        )
        self.assertEqual(res.status_code, 404)

        self.client.post(
            f"/api/monday-review/tasks/{b['id']}/notes",
            json={"kind": "persistent", "body": "n"},
            headers=self.admin,
        )
        self.client.post(
            f"/api/monday-review/tasks/{b['id']}/complete", json={}, headers=self.admin
        )
        res = self.client.delete(
            f"/api/monday-review/tasks/{b['id']}", headers=self.admin
        )
        self.assertEqual(res.status_code, 204)
        self.assertEqual([t["title"] for t in self._board()["tasks"]], ["C", "A"])
        with self.factory() as session:
            self.assertEqual(session.query(svc.MondayReviewTaskNote).count(), 0)
            self.assertEqual(session.query(svc.MondayReviewCompletion).count(), 0)
        res = self.client.delete(
            f"/api/monday-review/tasks/{b['id']}", headers=self.admin
        )
        self.assertEqual(res.status_code, 404)


class WeekHelperTests(unittest.TestCase):
    def test_monday_math(self) -> None:
        self.assertEqual(svc.monday_of(date(2026, 10, 14)), THIS_MONDAY)
        self.assertEqual(svc.monday_of(THIS_MONDAY), THIS_MONDAY)
        self.assertEqual(svc.monday_of(date(2026, 10, 18)), THIS_MONDAY)
        self.assertEqual(svc.current_week_of(THIS_MONDAY), THIS_MONDAY)
        self.assertEqual(svc.next_week_of(THIS_MONDAY), NEXT_MONDAY)
        self.assertEqual(svc.parse_week_of("2026-10-12"), THIS_MONDAY)
        with self.assertRaises(ValueError):
            svc.parse_week_of("2026-10-13")
        with self.assertRaises(ValueError):
            svc.parse_week_of("not a date")
        with self.assertRaises(ValueError):
            svc.parse_week_of(None)


if __name__ == "__main__":
    unittest.main()
