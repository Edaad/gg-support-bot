"""The web app answers OAuth discovery probes with 404, not the SPA page."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class WellKnownRoutesTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(
            os.environ, {"DATABASE_URL": "sqlite://", "MCP_ENABLED": "false"}
        )
        env.start()
        self.addCleanup(env.stop)
        from api.app import create_app

        self.app = create_app()

    def _client(self) -> TestClient:
        # No lifespan: the schema guard needs a migrated database.
        return TestClient(self.app)

    def test_well_known_paths_return_json_404(self):
        client = self._client()
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
            "/.well-known/oauth-authorization-server",
            "/.well-known/openid-configuration",
        ):
            response = client.get(path)
            self.assertEqual(response.status_code, 404, path)
            self.assertEqual(response.headers["content-type"], "application/json", path)
            self.assertEqual(response.json(), {"detail": "Not Found"}, path)

    def test_api_catch_all_still_404(self):
        response = self._client().get("/api/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_mcp_route_absent_when_disabled(self):
        response = self._client().post("/mcp")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
