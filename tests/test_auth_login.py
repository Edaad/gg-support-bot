"""Dashboard auth: dual-password login and JWT role claim."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient

from api.app import app
from api.auth import (
    ROLE_ACCOUNT_MANAGER,
    ROLE_ADMIN,
    create_token,
    resolve_role,
    _get_secret,
)


class ResolveRoleTests(unittest.TestCase):
    def test_admin_password(self):
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "admin-secret"}, clear=False):
            # Reset cached secret if tests touch _get_secret elsewhere
            import api.auth as auth_mod

            auth_mod._SECRET = None
            self.assertEqual(resolve_role("admin-secret"), ROLE_ADMIN)

    def test_am_password(self):
        with patch.dict(
            os.environ,
            {"DASHBOARD_PASSWORD": "admin-secret", "DASHBOARD_AM_PASSWORD": "am-secret"},
            clear=False,
        ):
            import api.auth as auth_mod

            auth_mod._SECRET = None
            self.assertEqual(resolve_role("am-secret"), ROLE_ACCOUNT_MANAGER)

    def test_am_unset_rejects(self):
        env = {"DASHBOARD_PASSWORD": "admin-secret"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("DASHBOARD_AM_PASSWORD", None)
            import api.auth as auth_mod

            auth_mod._SECRET = None
            self.assertIsNone(resolve_role("am-secret"))

    def test_admin_wins_when_passwords_match(self):
        with patch.dict(
            os.environ,
            {"DASHBOARD_PASSWORD": "same", "DASHBOARD_AM_PASSWORD": "same"},
            clear=False,
        ):
            import api.auth as auth_mod

            auth_mod._SECRET = None
            self.assertEqual(resolve_role("same"), ROLE_ADMIN)


class LoginRouteTests(unittest.TestCase):
    def setUp(self):
        import api.auth as auth_mod

        auth_mod._SECRET = None
        self.env_patch = patch.dict(
            os.environ,
            {"DASHBOARD_PASSWORD": "admin-secret", "DASHBOARD_AM_PASSWORD": "am-secret"},
            clear=False,
        )
        self.env_patch.start()
        auth_mod._SECRET = None
        self.client = TestClient(app)

    def tearDown(self):
        import api.auth as auth_mod

        auth_mod._SECRET = None
        self.env_patch.stop()

    def test_admin_login(self):
        r = self.client.post("/api/auth/login", json={"password": "admin-secret"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["role"], ROLE_ADMIN)
        self.assertIn("token", body)
        payload = jwt.decode(body["token"], _get_secret(), algorithms=["HS256"])
        self.assertEqual(payload["role"], ROLE_ADMIN)

    def test_am_login(self):
        r = self.client.post("/api/auth/login", json={"password": "am-secret"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["role"], ROLE_ACCOUNT_MANAGER)
        payload = jwt.decode(body["token"], _get_secret(), algorithms=["HS256"])
        self.assertEqual(payload["role"], ROLE_ACCOUNT_MANAGER)

    def test_invalid_password(self):
        r = self.client.post("/api/auth/login", json={"password": "wrong"})
        self.assertEqual(r.status_code, 401)

    def test_create_token_default_admin(self):
        token = create_token()
        payload = jwt.decode(token, _get_secret(), algorithms=["HS256"])
        self.assertEqual(payload.get("role"), ROLE_ADMIN)


if __name__ == "__main__":
    unittest.main()
