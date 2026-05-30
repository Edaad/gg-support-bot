"""API tests for payments dashboard routes."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import create_token, get_current_admin
from api.routes.payments import router
from db.connection import get_db_dependency

TOKEN = create_token()


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    def override_admin():
        return "admin"

    def override_db():
        yield MagicMock()

    app.dependency_overrides[get_current_admin] = override_admin
    app.dependency_overrides[get_db_dependency] = override_db
    return app


class PaymentsApiTestCase(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {"DASHBOARD_PASSWORD": "changeme"}, clear=False)
        self.env_patch.start()
        self.client = TestClient(_make_app())

    def tearDown(self):
        self.env_patch.stop()

    def test_providers_requires_auth(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        response = client.get("/api/payments/providers")
        self.assertIn(response.status_code, (401, 403))

    def test_providers_success(self):
        response = self.client.get(
            "/api/payments/providers",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], "stripe")

    def test_customers_club_not_found(self):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_admin] = lambda: "admin"

        def override_db():
            yield mock_db

        app.dependency_overrides[get_db_dependency] = override_db
        client = TestClient(app)

        response = client.get(
            "/api/payments/stripe/customers?club_id=999",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
