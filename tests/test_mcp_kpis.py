"""get_kpis MCP tool against the seeded KPI database from test_kpis."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from mcp.server.mcpserver.exceptions import ToolError

import mcp_server.db as mcp_db
from mcp_server.tools.analytics import get_kpis
from tests.test_kpis import _SeededDb


class GetKpisToolTests(_SeededDb):
    def setUp(self) -> None:
        super().setUp()
        env = patch.dict(
            os.environ,
            {"MCP_API_TOKENS": "dev:" + "x" * 32, "MCP_ACTOR": "tester"},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)
        mcp_db.session_factory = self.factory
        self.addCleanup(setattr, mcp_db, "session_factory", None)

    def test_week_by_club_key(self) -> None:
        out = get_kpis(granularity="week", date_="2026-10-07", club="clubgto")
        item = out["item"]
        self.assertEqual(item["period"]["start"], "2026-10-05")
        self.assertEqual(item["club_name"], "ClubGTO")
        self.assertEqual(item["current"]["deposits"]["count"], 2)
        self.assertEqual(item["current"]["deposits"]["amount_usd"], 240.0)
        self.assertEqual(item["by_club"], [])
        self.assertTrue(any("Monday-Sunday" in n for n in out["notes"]))

    def test_all_clubs_split(self) -> None:
        item = get_kpis(date_="2026-10-05")["item"]
        self.assertEqual(
            [c["club_name"] for c in item["by_club"]],
            ["Round Table", "Creator Club", "ClubGTO"],
        )
        self.assertEqual(
            sum(c["deposits_count"] for c in item["by_club"]),
            item["current"]["deposits"]["count"],
        )

    def test_bad_input(self) -> None:
        with self.assertRaises(ToolError):
            get_kpis(granularity="custom", from_="2026-10-01")
        with self.assertRaises(ToolError):
            get_kpis(granularity="fortnight")


if __name__ == "__main__":
    unittest.main()
