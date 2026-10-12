"""build_server(): tool catalogue, annotations, resources and prompts."""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from mcp.client import Client

from mcp_server.server import build_server
from mcp_server.tools import WRITE_TOOL_NAMES

EXPECTED_RESOURCES = {
    "gg://reference/clubs",
    "gg://reference/vocabulary",
    "gg://reference/enums",
    "gg://reference/conventions",
    "gg://reference/caveats",
}


class ToolCatalogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.env = patch.dict(
            os.environ, {"MCP_API_TOKENS": "dev:" + "x" * 32}, clear=False
        )
        cls.env.start()
        cls.server = build_server()
        cls.tools = asyncio.run(cls.server.list_tools())
        cls.by_name = {tool.name: tool for tool in cls.tools}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.env.stop()

    def test_server_identity(self) -> None:
        self.assertEqual(self.server.name, "gg-dashboard")
        self.assertIn("list_clubs", self.server.instructions or "")

    def test_at_least_forty_tools_with_unique_names(self) -> None:
        self.assertGreaterEqual(len(self.tools), 40)
        self.assertEqual(len(self.by_name), len(self.tools))

    def test_every_tool_has_a_description(self) -> None:
        for tool in self.tools:
            with self.subTest(tool=tool.name):
                self.assertTrue((tool.description or "").strip())
                self.assertNotIn("endpoint", tool.description.lower())

    def test_write_tools_are_registered_and_flagged(self) -> None:
        self.assertTrue(WRITE_TOOL_NAMES)
        for name in WRITE_TOOL_NAMES:
            with self.subTest(tool=name):
                self.assertIn(name, self.by_name)
                tool = self.by_name[name]
                self.assertIsNotNone(tool.annotations)
                self.assertFalse(tool.annotations.read_only_hint)
                properties = tool.input_schema.get("properties", {})
                self.assertIn("confirm", properties)
                self.assertEqual(properties["confirm"].get("type"), "boolean")
                self.assertEqual(properties["confirm"].get("default"), False)
                self.assertNotIn("ctx", properties)
                self.assertNotIn("confirm", tool.input_schema.get("required", []))

    def test_read_tools_are_read_only(self) -> None:
        read_tools = [t for t in self.tools if t.name not in WRITE_TOOL_NAMES]
        self.assertGreater(len(read_tools), 30)
        for tool in read_tools:
            with self.subTest(tool=tool.name):
                self.assertIsNotNone(tool.annotations)
                self.assertTrue(tool.annotations.read_only_hint)
                self.assertFalse(tool.annotations.destructive_hint)
                self.assertNotIn("confirm", tool.input_schema.get("properties", {}))
                self.assertNotIn("ctx", tool.input_schema.get("properties", {}))

    def test_core_tools_present(self) -> None:
        for name in (
            "list_clubs",
            "get_club",
            "list_club_groups",
            "find_group",
            "list_payment_methods",
            "search_payments",
            "list_cashout_records",
            "list_mcp_tool_calls",
            "get_dashboard_overview",
        ):
            with self.subTest(tool=name):
                self.assertIn(name, self.by_name)

    def test_paging_parameters_follow_convention(self) -> None:
        for tool in self.tools:
            properties = tool.input_schema.get("properties", {})
            if "limit" in properties:
                with self.subTest(tool=tool.name):
                    self.assertEqual(properties["limit"].get("type"), "integer")
            if "include_pii" in properties:
                with self.subTest(tool=tool.name):
                    self.assertEqual(properties["include_pii"].get("type"), "boolean")
                    self.assertEqual(properties["include_pii"].get("default"), False)


class ResourcesAndPromptsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {"MCP_API_TOKENS": "dev:" + "x" * 32}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.server = build_server()

    async def test_reference_resources_list_and_read(self) -> None:
        async with Client(self.server) as client:
            listed = await client.list_resources()
            uris = {str(r.uri) for r in listed.resources}
            self.assertEqual(uris, EXPECTED_RESOURCES)
            for resource in listed.resources:
                with self.subTest(uri=str(resource.uri)):
                    self.assertEqual(resource.mime_type, "text/markdown")
                    self.assertTrue((resource.description or "").strip())
                    result = await client.read_resource(resource.uri)
                    self.assertEqual(len(result.contents), 1)
                    text = getattr(result.contents[0], "text", "")
                    self.assertTrue(text.strip())
                    self.assertTrue(text.startswith("#"))

    async def test_clubs_resource_mentions_every_club(self) -> None:
        async with Client(self.server) as client:
            result = await client.read_resource("gg://reference/clubs")
        text = result.contents[0].text
        for needle in ("Round Table", "Creator Club", "ClubGTO", "aces-table", "GTO"):
            self.assertIn(needle, text)

    async def test_enums_resource_lists_cashout_statuses(self) -> None:
        async with Client(self.server) as client:
            result = await client.read_resource("gg://reference/enums")
        text = result.contents[0].text
        self.assertIn("cashout record status", text)
        self.assertIn("ok, error, refused", text)

    async def test_prompts(self) -> None:
        async with Client(self.server) as client:
            listed = await client.list_prompts()
            names = {p.name for p in listed.prompts}
            self.assertEqual(
                names, {"investigate_player", "daily_ops_check", "monday_review"}
            )
            investigate = next(
                p for p in listed.prompts if p.name == "investigate_player"
            )
            self.assertEqual([a.name for a in investigate.arguments], ["query"])
            self.assertTrue(investigate.arguments[0].required)

            result = await client.get_prompt(
                "investigate_player", {"query": "8190-5287"}
            )
            text = result.messages[0].content.text
            self.assertIn("8190-5287", text)
            self.assertIn("find_group", text)

            result = await client.get_prompt("daily_ops_check", {})
            text = result.messages[0].content.text
            self.assertIn("yesterday", text)
            self.assertIn("get_dashboard_overview", text)


if __name__ == "__main__":
    unittest.main()
