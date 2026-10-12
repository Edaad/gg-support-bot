"""MCP server settings (token parsing) and bearer auth / actor attribution."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from mcp_server.auth import (
    UNKNOWN_ACTOR,
    BearerAuthASGIMiddleware,
    actor_for_token,
    actor_from_headers,
    extract_bearer,
)
from mcp_server.settings import (
    DEFAULT_STATEMENT_TIMEOUT_MS,
    MIN_TOKEN_LENGTH,
    load_settings,
    parse_tokens,
)

GOOD_TOKEN = "x" * 32
OTHER_TOKEN = "y" * 32
MCP_ENV_KEYS = (
    "MCP_API_TOKENS",
    "MCP_ACTOR",
    "MCP_ENABLED",
    "MCP_STATEMENT_TIMEOUT_MS",
)


def _env_without_mcp() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in MCP_ENV_KEYS}


class ParseTokensTests(unittest.TestCase):
    def test_valid_entries(self) -> None:
        parsed = parse_tokens(f"dev:{GOOD_TOKEN}, ops:{OTHER_TOKEN}")
        self.assertEqual(parsed, {"dev": GOOD_TOKEN, "ops": OTHER_TOKEN})

    def test_empty_and_none(self) -> None:
        self.assertEqual(parse_tokens(None), {})
        self.assertEqual(parse_tokens(""), {})
        self.assertEqual(parse_tokens(" , "), {})

    def test_malformed_entry_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_tokens("nocolon")
        with self.assertRaises(ValueError):
            parse_tokens(f":{GOOD_TOKEN}")
        with self.assertRaises(ValueError):
            parse_tokens("dev:")

    def test_invalid_actor_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_tokens(f"bad name:{GOOD_TOKEN}")

    def test_duplicate_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_tokens(f"dev:{GOOD_TOKEN},dev:{OTHER_TOKEN}")

    def test_short_token_warns_but_is_accepted(self) -> None:
        short = "s" * (MIN_TOKEN_LENGTH - 1)
        with self.assertLogs("mcp_server.settings", level="WARNING") as logs:
            parsed = parse_tokens(f"dev:{short}")
        self.assertEqual(parsed, {"dev": short})
        self.assertTrue(any("shorter than" in line for line in logs.output))


class LoadSettingsTests(unittest.TestCase):
    def test_defaults(self) -> None:
        with patch.dict(os.environ, _env_without_mcp(), clear=True):
            settings = load_settings()
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.tokens, {})
        self.assertEqual(settings.statement_timeout_ms, DEFAULT_STATEMENT_TIMEOUT_MS)
        self.assertEqual(settings.actor, "stdio")

    def test_env_values(self) -> None:
        env = _env_without_mcp()
        env.update(
            {
                "MCP_ENABLED": "true",
                "MCP_API_TOKENS": f"dev:{GOOD_TOKEN}",
                "MCP_STATEMENT_TIMEOUT_MS": "2500",
                "MCP_ACTOR": "laptop",
            }
        )
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.tokens, {"dev": GOOD_TOKEN})
        self.assertEqual(settings.statement_timeout_ms, 2500)
        self.assertEqual(settings.actor, "laptop")

    def test_timeout_floor_and_bad_value(self) -> None:
        env = _env_without_mcp()
        env["MCP_STATEMENT_TIMEOUT_MS"] = "10"
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(load_settings().statement_timeout_ms, 1_000)
        env["MCP_STATEMENT_TIMEOUT_MS"] = "abc"
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                load_settings().statement_timeout_ms, DEFAULT_STATEMENT_TIMEOUT_MS
            )


async def _echo_actor_app(scope, receive, send) -> None:
    actor = scope.get("state", {}).get("mcp_actor", "")
    body = actor.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": body})


class BearerAuthMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(
            BearerAuthASGIMiddleware(_echo_actor_app, {"dev": GOOD_TOKEN})
        )

    def test_missing_header_is_401_with_challenge(self) -> None:
        response = self.client.get("/mcp")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("www-authenticate"), "Bearer")
        self.assertEqual(response.json(), {"error": "unauthorized"})

    def test_wrong_token_is_401(self) -> None:
        response = self.client.get(
            "/mcp", headers={"Authorization": f"Bearer {OTHER_TOKEN}"}
        )
        self.assertEqual(response.status_code, 401)

    def test_wrong_scheme_is_401(self) -> None:
        response = self.client.get(
            "/mcp", headers={"Authorization": f"Basic {GOOD_TOKEN}"}
        )
        self.assertEqual(response.status_code, 401)

    def test_right_token_sets_actor_on_scope_state(self) -> None:
        response = self.client.get(
            "/mcp", headers={"Authorization": f"Bearer {GOOD_TOKEN}"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "dev")

    def test_no_tokens_configured_is_503(self) -> None:
        client = TestClient(BearerAuthASGIMiddleware(_echo_actor_app, {}))
        response = client.get("/mcp", headers={"Authorization": f"Bearer {GOOD_TOKEN}"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("MCP_API_TOKENS", response.json()["error"])


class ActorResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = patch.dict(
            os.environ,
            {"MCP_API_TOKENS": f"dev:{GOOD_TOKEN}", "MCP_ACTOR": "tester"},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_extract_bearer_from_raw_asgi_headers(self) -> None:
        raw = [(b"authorization", f"Bearer {GOOD_TOKEN}".encode("latin-1"))]
        self.assertEqual(extract_bearer(raw), GOOD_TOKEN)
        self.assertIsNone(extract_bearer([(b"authorization", b"Bearer ")]))
        self.assertIsNone(extract_bearer(None))

    def test_actor_for_token(self) -> None:
        tokens = {"dev": GOOD_TOKEN, "ops": OTHER_TOKEN}
        self.assertEqual(actor_for_token(GOOD_TOKEN, tokens), "dev")
        self.assertEqual(actor_for_token(OTHER_TOKEN, tokens), "ops")
        self.assertIsNone(actor_for_token("z" * 32, tokens))
        self.assertIsNone(actor_for_token(None, tokens))

    def test_no_headers_means_stdio_actor_from_env(self) -> None:
        self.assertEqual(actor_from_headers(None), "tester")

    def test_header_with_known_token_gives_name(self) -> None:
        headers = {"Authorization": f"Bearer {GOOD_TOKEN}"}
        self.assertEqual(actor_from_headers(headers), "dev")

    def test_unknown_token_is_unknown(self) -> None:
        headers = {"authorization": f"Bearer {OTHER_TOKEN}"}
        self.assertEqual(actor_from_headers(headers), UNKNOWN_ACTOR)
        self.assertEqual(actor_from_headers({}), UNKNOWN_ACTOR)


if __name__ == "__main__":
    unittest.main()
