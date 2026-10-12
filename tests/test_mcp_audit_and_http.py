"""Audit middleware (mcp_tool_calls rows) over the in-memory and HTTP transports."""

from __future__ import annotations

import os
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.client import Client

import mcp_server.db as mcp_db
from db.models import Club, Expense, McpToolCall
from mcp_server.audit import outcome_for_result, redacted_args
from mcp_server.http import build_http_route
from mcp_server.server import build_server
from tests.support.sqlite_db import make_sqlite_session_factory

TOKEN = "x" * 32
ENV = {"MCP_API_TOKENS": f"dev:{TOKEN}", "MCP_ACTOR": "tester"}
TABLES = [
    "clubs",
    "groups",
    "club_linked_accounts",
    "club_payment_methods",
    "plaid_items",
    "player_details",
    "custom_commands",
    "support_group_chats",
    "expenses",
    "outbound_sends",
    "mcp_tool_calls",
]


def _seed(factory) -> None:
    with factory() as session:
        session.add(Club(id=1, name="ClubGTO", telegram_user_id=1001))
        session.commit()


def _audit_rows(factory) -> list[McpToolCall]:
    with factory() as session:
        return session.query(McpToolCall).order_by(McpToolCall.id).all()


class RedactedArgsAndOutcomeTests(unittest.TestCase):
    def test_redacted_args_masks_pii_and_drops_secrets(self) -> None:
        out = redacted_args({"tag": "@someone", "token": "t", "limit": 5})
        self.assertEqual(out, {"tag": "@so***", "limit": 5})
        self.assertIsNone(redacted_args(None))
        self.assertIsNone(redacted_args(["not", "a", "dict"]))

    def test_redacted_args_caps_size(self) -> None:
        out = redacted_args({"q": "x" * 10_000, "limit": 1})
        self.assertEqual(out, {"_truncated": True, "keys": ["limit", "q"]})

    def test_outcome_for_dict_results(self) -> None:
        self.assertEqual(outcome_for_result(None), "ok")
        self.assertEqual(outcome_for_result({"isError": True, "content": []}), "error")
        self.assertEqual(
            outcome_for_result(
                {"isError": False, "structuredContent": {"refused": True}}
            ),
            "refused",
        )
        self.assertEqual(
            outcome_for_result(
                {"isError": False, "structuredContent": {"result": {"refused": True}}}
            ),
            "refused",
        )
        self.assertEqual(
            outcome_for_result({"isError": False, "structuredContent": {"items": []}}),
            "ok",
        )
        self.assertEqual(
            outcome_for_result(
                {"content": [{"type": "text", "text": '{"refused": true}'}]}
            ),
            "refused",
        )


class InMemoryAuditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, ENV, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.factory = make_sqlite_session_factory(TABLES)
        mcp_db.session_factory = self.factory
        self.addCleanup(setattr, mcp_db, "session_factory", None)
        _seed(self.factory)
        self.server = build_server()

    async def test_read_tool_is_logged_ok(self) -> None:
        async with Client(self.server) as client:
            result = await client.call_tool("list_clubs", {})
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["returned"], 1)
        rows = _audit_rows(self.factory)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.tool, "list_clubs")
        self.assertEqual(row.outcome, "ok")
        self.assertEqual(row.transport, "stdio")
        self.assertEqual(row.actor, "tester")
        self.assertFalse(row.is_write)
        self.assertEqual(row.args_json, {})
        self.assertIsNone(row.error_message)
        self.assertGreaterEqual(row.duration_ms, 0)
        self.assertIsNotNone(row.created_at)

    async def test_args_are_redacted_in_the_log(self) -> None:
        async with Client(self.server) as client:
            # ``tag`` is a PII (handle) key: stored masked whatever the outcome.
            await client.call_tool(
                "list_outbound_sends", {"tag": "@someone", "limit": 3}
            )
        rows = _audit_rows(self.factory)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].tool, "list_outbound_sends")
        self.assertEqual(rows[0].args_json, {"tag": "@so***", "limit": 3})

    async def test_write_tool_dry_run_is_logged_refused(self) -> None:
        async with Client(self.server) as client:
            result = await client.call_tool(
                "create_expense",
                {"amount": 5, "expense_type": "Rakeback", "club": "clubgto"},
            )
        self.assertFalse(result.is_error)
        self.assertTrue(result.structured_content["refused"])
        with self.factory() as session:
            self.assertEqual(session.query(Expense).count(), 0)
        rows = _audit_rows(self.factory)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.tool, "create_expense")
        self.assertEqual(row.outcome, "refused")
        self.assertTrue(row.is_write)
        self.assertEqual(
            row.args_json, {"amount": 5, "expense_type": "Rakeback", "club": "clubgto"}
        )

    async def test_write_tool_confirm_is_logged_ok(self) -> None:
        async with Client(self.server) as client:
            result = await client.call_tool(
                "create_expense",
                {
                    "amount": 5,
                    "expense_type": "Rakeback",
                    "club": "clubgto",
                    "confirm": True,
                },
            )
        self.assertFalse(result.is_error)
        self.assertTrue(result.structured_content["applied"])
        with self.factory() as session:
            self.assertEqual(session.query(Expense).count(), 1)
        row = _audit_rows(self.factory)[0]
        self.assertEqual(row.outcome, "ok")
        self.assertTrue(row.is_write)

    async def test_tool_error_is_logged_error(self) -> None:
        async with Client(self.server) as client:
            result = await client.call_tool("get_club", {"club": "nope"})
        self.assertTrue(result.is_error)
        self.assertIn("unknown club", result.content[0].text)
        rows = _audit_rows(self.factory)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.tool, "get_club")
        self.assertEqual(row.outcome, "error")
        self.assertFalse(row.is_write)
        self.assertIn("unknown club", row.error_message or "")
        self.assertEqual(row.args_json, {"club": "nope"})

    async def test_list_mcp_tool_calls_reads_the_log(self) -> None:
        async with Client(self.server) as client:
            await client.call_tool("list_clubs", {})
            result = await client.call_tool(
                "list_mcp_tool_calls", {"tool": "list_clubs"}
            )
        self.assertFalse(result.is_error)
        items = result.structured_content["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["tool"], "list_clubs")
        self.assertEqual(items[0]["actor"], "tester")
        self.assertEqual(items[0]["outcome"], "ok")
        # The read of the log is itself logged afterwards.
        self.assertEqual(
            [r.tool for r in _audit_rows(self.factory)],
            [
                "list_clubs",
                "list_mcp_tool_calls",
            ],
        )


HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "tests", "version": "1"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def _call(request_id: int, name: str, arguments: dict | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }


class HttpTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, ENV, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.factory = make_sqlite_session_factory(TABLES)
        mcp_db.session_factory = self.factory
        self.addCleanup(setattr, mcp_db, "session_factory", None)
        _seed(self.factory)

        server = build_server()
        route = build_http_route(server, {"dev": TOKEN})
        self.assertEqual(route.path, "/mcp")
        self.assertEqual(set(route.methods), {"POST"})

        @asynccontextmanager
        async def lifespan(_app):
            async with server.session_manager.run():
                yield

        app = FastAPI(lifespan=lifespan)
        app.router.routes.append(route)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_initialize_without_bearer_is_401(self) -> None:
        response = self.client.post("/mcp", json=INIT)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("www-authenticate"), "Bearer")
        self.assertEqual(_audit_rows(self.factory), [])

    def test_wrong_bearer_is_401(self) -> None:
        headers = dict(HEADERS, Authorization="Bearer " + "y" * 32)
        response = self.client.post("/mcp", headers=headers, json=INIT)
        self.assertEqual(response.status_code, 401)

    def test_initialize_with_bearer_returns_json(self) -> None:
        response = self.client.post("/mcp", headers=HEADERS, json=INIT)
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response.headers.get("content-type", ""))
        body = response.json()
        self.assertEqual(body["id"], 1)
        self.assertEqual(body["result"]["serverInfo"]["name"], "gg-dashboard")
        self.assertIn("tools", body["result"]["capabilities"])
        # initialize is not a tools/call: nothing is logged.
        self.assertEqual(_audit_rows(self.factory), [])

    def test_tools_call_over_http_is_logged_with_token_actor(self) -> None:
        self.assertEqual(
            self.client.post("/mcp", headers=HEADERS, json=INIT).status_code, 200
        )
        self.assertIn(
            self.client.post("/mcp", headers=HEADERS, json=INITIALIZED).status_code,
            (200, 202),
        )
        response = self.client.post(
            "/mcp", headers=HEADERS, json=_call(2, "list_clubs")
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("items", response.text)
        body = response.json()
        self.assertEqual(body["id"], 2)
        self.assertFalse(body["result"].get("isError", False))
        self.assertEqual(body["result"]["structuredContent"]["returned"], 1)

        response = self.client.post(
            "/mcp", headers=HEADERS, json=_call(3, "get_club", {"club": "nope"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["result"]["isError"])

        rows = _audit_rows(self.factory)
        self.assertEqual([r.tool for r in rows], ["list_clubs", "get_club"])
        for row in rows:
            self.assertEqual(row.transport, "http")
            self.assertEqual(row.actor, "dev")
        self.assertEqual(rows[0].outcome, "ok")
        self.assertEqual(rows[1].outcome, "error")

    def test_get_is_method_not_allowed(self) -> None:
        response = self.client.get("/mcp", headers=HEADERS)
        self.assertEqual(response.status_code, 405)

    def test_delete_is_method_not_allowed(self) -> None:
        response = self.client.delete("/mcp", headers=HEADERS)
        self.assertEqual(response.status_code, 405)


if __name__ == "__main__":
    unittest.main()
