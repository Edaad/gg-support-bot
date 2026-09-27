"""Plaid bank-login API: tokens, status, roles, and slot count."""

from __future__ import annotations

import hashlib
import json
import os
import time
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import jwt
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.auth import ROLE_ACCOUNT_MANAGER, ROLE_GTO, create_token
from api.plaid_bank import (
    apply_transaction_updates,
    decrypt_access_token,
    encrypt_access_token,
    is_zelle_transaction,
    pull_transaction_updates,
    status_from_item,
)
from api.plaid_webhook import PlaidWebhookError, verify_plaid_webhook
from api.routes.plaid_items import router
from api.routes.plaid_webhook import router as plaid_webhook_router
from db.connection import get_db_dependency
from db.models import PlaidItem, PlaidTransaction

TOKEN_KEY = Fernet.generate_key().decode()
ACCESS_TOKEN = "access-sandbox-test-token"


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE clubs (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO clubs (id, name) VALUES (1, 'ClubGTO'), (2, 'Round Table')"
            )
        )
    PlaidItem.__table__.create(bind=engine)
    PlaidTransaction.__table__.create(bind=engine)
    return sessionmaker(bind=engine)


def _app(session_factory):
    app = FastAPI()
    app.include_router(router)

    def _db():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db_dependency] = _db
    return app


def _env():
    return patch.dict(
        os.environ,
        {
            "PLAID_CLIENT_ID": "client-id",
            "PLAID_SECRET": "client-secret",
            "PLAID_ENV": "production",
            "PLAID_TOKEN_KEY": TOKEN_KEY,
        },
        clear=False,
    )


class PlaidStatusTest(unittest.TestCase):
    def test_status_from_item(self):
        self.assertEqual(status_from_item({}), ("connected", None))
        self.assertEqual(status_from_item({"error": None}), ("connected", None))
        status, code = status_from_item(
            {"error": {"error_code": "ITEM_LOGIN_REQUIRED"}}
        )
        self.assertEqual(status, "needs_sign_in")
        self.assertEqual(code, "ITEM_LOGIN_REQUIRED")

    def test_encrypt_roundtrip(self):
        with _env():
            blob = encrypt_access_token(ACCESS_TOKEN)
            self.assertNotIn(ACCESS_TOKEN, blob)
            self.assertEqual(decrypt_access_token(blob), ACCESS_TOKEN)


class PlaidItemsApiTest(unittest.TestCase):
    def setUp(self):
        self.factory = _session_factory()
        self.client = TestClient(_app(self.factory))
        self.env = _env()
        self.env.start()
        self.admin = create_token()

    def tearDown(self):
        self.env.stop()

    def _auth(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_account_manager_forbidden(self):
        response = self.client.get(
            "/api/clubs/1/plaid/items",
            headers=self._auth(create_token(ROLE_ACCOUNT_MANAGER)),
        )
        self.assertEqual(response.status_code, 403)

    def test_gto_other_club_forbidden(self):
        with patch("api.gto_club.lookup_gto_club_id", return_value=1):
            response = self.client.get(
                "/api/clubs/2/plaid/items",
                headers=self._auth(create_token(ROLE_GTO)),
            )
        self.assertEqual(response.status_code, 403)

    def test_exchange_is_idempotent_and_hidden(self):
        calls = {"n": 0}

        def fake_post(path, body):
            self.assertNotIn("secret", body)
            calls["n"] += 1
            return {
                "item_id": "item-1",
                "access_token": f"access-secret-{calls['n']}",
            }

        with patch("api.plaid_bank.plaid_post", side_effect=fake_post):
            first = self.client.post(
                "/api/clubs/1/plaid/items",
                headers=self._auth(self.admin),
                json={
                    "public_token": "public-sandbox-1",
                    "institution_id": "ins_1",
                    "institution_name": "Chase",
                },
            )
            second = self.client.post(
                "/api/clubs/1/plaid/items",
                headers=self._auth(self.admin),
                json={
                    "public_token": "public-sandbox-2",
                    "institution_id": "ins_1",
                    "institution_name": "Chase",
                },
            )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertNotIn("access_token", first.json())
        self.assertNotIn("access-secret", first.text)
        self.assertNotIn("public_token", first.json())

        session = self.factory()
        rows = session.query(PlaidItem).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            decrypt_access_token(rows[0].access_token_encrypted), "access-secret-2"
        )
        session.close()

    def test_slots_used_counts_every_club(self):
        session = self.factory()
        session.add(
            PlaidItem(
                club_id=2,
                item_id="item-other",
                institution_name="Wells Fargo",
                access_token_encrypted=encrypt_access_token("access-other"),
            )
        )
        session.commit()
        session.close()

        def fake_post(path, body):
            if path == "/item/public_token/exchange":
                return {"item_id": "item-gto", "access_token": ACCESS_TOKEN}
            if path == "/item/get":
                return {"item": {"item_id": "item-gto", "error": None}}
            raise AssertionError(path)

        with patch("api.plaid_bank.plaid_post", side_effect=fake_post):
            created = self.client.post(
                "/api/clubs/1/plaid/items",
                headers=self._auth(self.admin),
                json={"public_token": "public-sandbox", "institution_name": "Chase"},
            )
            listed = self.client.get(
                "/api/clubs/1/plaid/items",
                headers=self._auth(self.admin),
            )

        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(listed.status_code, 200, listed.text)
        body = listed.json()
        self.assertEqual(body["slots_used"], 2)
        self.assertEqual(body["slot_cap"], 10)
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["institution_name"], "Chase")
        self.assertEqual(body["items"][0]["status"], "connected")
        self.assertNotIn(ACCESS_TOKEN, listed.text)
        self.assertNotIn("access_token_encrypted", listed.text)

    def test_login_required_status(self):
        session = self.factory()
        session.add(
            PlaidItem(
                club_id=1,
                item_id="item-stale",
                institution_name="Chase",
                access_token_encrypted=encrypt_access_token(ACCESS_TOKEN),
            )
        )
        session.commit()
        session.close()

        def fake_post(path, body):
            self.assertEqual(body.get("access_token"), ACCESS_TOKEN)
            return {
                "item": {
                    "item_id": "item-stale",
                    "error": {"error_code": "ITEM_LOGIN_REQUIRED"},
                }
            }

        with patch("api.plaid_bank.plaid_post", side_effect=fake_post):
            listed = self.client.get(
                "/api/clubs/1/plaid/items",
                headers=self._auth(self.admin),
            )
        self.assertEqual(listed.status_code, 200, listed.text)
        item = listed.json()["items"][0]
        self.assertEqual(item["status"], "needs_sign_in")
        self.assertEqual(item["error_code"], "ITEM_LOGIN_REQUIRED")

    def test_update_mode_reuses_token(self):
        session = self.factory()
        row = PlaidItem(
            club_id=1,
            item_id="item-repair",
            institution_name="Chase",
            access_token_encrypted=encrypt_access_token(ACCESS_TOKEN),
        )
        session.add(row)
        session.commit()
        row_id = row.id
        session.close()

        seen = {}

        def fake_post(path, body):
            seen["path"] = path
            seen["body"] = body
            return {"link_token": "link-sandbox-repair"}

        with patch("api.plaid_bank.plaid_post", side_effect=fake_post):
            response = self.client.post(
                "/api/clubs/1/plaid/link-token",
                headers=self._auth(self.admin),
                json={"item_id": row_id},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(seen["path"], "/link/token/create")
        self.assertEqual(seen["body"]["access_token"], ACCESS_TOKEN)
        self.assertNotIn("products", seen["body"])
        self.assertEqual(response.json()["link_token"], "link-sandbox-repair")

        session = self.factory()
        self.assertEqual(session.query(PlaidItem).count(), 1)
        session.close()

    def test_zelle_fields_and_list(self):
        zelle = {
            "transaction_id": "txn-zelle",
            "account_id": "acc-1",
            "amount": -200,
            "date": "2026-09-20",
            "name": "ZELLE FROM JANE DOE",
            "original_description": "ZELLE FROM JANE DOE MEMO lunch",
            "pending": False,
            "payment_channel": "other",
            "payment_meta": {
                "payer": "Jane Doe",
                "payee": "Club",
                "reason": "lunch",
                "payment_method": "Zelle",
            },
        }
        grocery = {
            "transaction_id": "txn-grocery",
            "amount": 12.5,
            "date": "2026-09-21",
            "name": "GROCERY STORE",
            "payment_meta": {},
        }
        self.assertTrue(is_zelle_transaction(zelle))
        self.assertFalse(is_zelle_transaction(grocery))

        def fake_post(path, body):
            self.assertEqual(path, "/transactions/sync")
            self.assertTrue(body["options"]["include_original_description"])
            if "cursor" not in body:
                return {
                    "added": [zelle],
                    "modified": [],
                    "removed": [],
                    "next_cursor": "cur-1",
                    "has_more": True,
                }
            self.assertEqual(body["cursor"], "cur-1")
            return {
                "added": [grocery],
                "modified": [],
                "removed": [],
                "next_cursor": "cur-2",
                "has_more": False,
            }

        with patch("api.plaid_bank.plaid_post", side_effect=fake_post):
            added, _modified, _removed, cursor = pull_transaction_updates(
                ACCESS_TOKEN, None
            )

        session = self.factory()
        item = PlaidItem(
            club_id=1,
            item_id="item-zelle",
            institution_name="Chase",
            access_token_encrypted=encrypt_access_token(ACCESS_TOKEN),
        )
        session.add(item)
        session.flush()
        apply_transaction_updates(session, item, added, [], [], cursor)
        session.commit()
        self.assertEqual(session.query(PlaidTransaction).count(), 2)
        saved = (
            session.query(PlaidTransaction)
            .filter(PlaidTransaction.transaction_id == "txn-zelle")
            .one()
        )
        self.assertTrue(saved.is_zelle)
        self.assertEqual(saved.memo, "lunch")
        self.assertEqual(saved.original_description, "ZELLE FROM JANE DOE MEMO lunch")
        self.assertEqual(saved.payer, "Jane Doe")
        session.close()

        listed = self.client.get(
            "/api/clubs/1/plaid/zelle",
            headers=self._auth(self.admin),
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        body = listed.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["memo"], "lunch")
        self.assertEqual(
            body["items"][0]["original_description"],
            "ZELLE FROM JANE DOE MEMO lunch",
        )
        self.assertNotIn(ACCESS_TOKEN, listed.text)
        self.assertNotIn("access_token", listed.text)

    def test_zelle_account_manager_forbidden(self):
        response = self.client.get(
            "/api/clubs/1/plaid/zelle",
            headers=self._auth(create_token(ROLE_ACCOUNT_MANAGER)),
        )
        self.assertEqual(response.status_code, 403)

    def _aware(self, value):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def test_incremental_first_seen_is_kept(self):
        detected = datetime(2026, 9, 27, 18, 0, tzinfo=timezone.utc)
        later = datetime(2026, 9, 27, 19, 0, tzinfo=timezone.utc)
        session = self.factory()
        item = PlaidItem(
            club_id=1,
            item_id="item-seen",
            institution_name="Chase",
            access_token_encrypted=encrypt_access_token(ACCESS_TOKEN),
            transactions_cursor="cur-existing",
        )
        session.add(item)
        session.commit()
        txn = {
            "transaction_id": "txn-seen",
            "amount": -10,
            "date": "2026-09-27",
            "name": "ZELLE FROM A B",
            "payment_meta": {},
        }
        apply_transaction_updates(
            session, item, [txn], [], [], "cur-2", detected_at=detected
        )
        session.commit()
        row = session.query(PlaidTransaction).one()
        self.assertEqual(self._aware(row.first_seen_at), detected)
        changed = dict(txn)
        changed["amount"] = -11
        apply_transaction_updates(
            session, item, [], [changed], [], "cur-3", detected_at=later
        )
        session.commit()
        session.refresh(row)
        self.assertEqual(self._aware(row.first_seen_at), detected)
        self.assertEqual(row.amount, Decimal("-11.00"))
        session.close()

    def test_initial_sync_is_not_timed(self):
        detected = datetime(2026, 9, 27, 18, 0, tzinfo=timezone.utc)
        session = self.factory()
        item = PlaidItem(
            club_id=1,
            item_id="item-backfill",
            institution_name="Chase",
            access_token_encrypted=encrypt_access_token(ACCESS_TOKEN),
        )
        session.add(item)
        session.commit()
        txn = {
            "transaction_id": "txn-old",
            "amount": -10,
            "date": "2026-09-01",
            "name": "ZELLE FROM A B",
            "payment_meta": {},
        }
        apply_transaction_updates(
            session, item, [txn], [], [], "cur-1", detected_at=detected
        )
        session.commit()
        row = session.query(PlaidTransaction).one()
        self.assertIsNone(row.first_seen_at)
        session.close()


def _es256_jwk():
    private = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(ECAlgorithm.to_jwk(private.public_key()))
    jwk["kid"] = "test-key"
    jwk["alg"] = "ES256"
    return private, jwk


class PlaidWebhookTest(unittest.TestCase):
    def test_verify_accepts_signed_body_and_rejects_tamper(self):
        private, jwk = _es256_jwk()
        body = b'{"webhook_type":"TRANSACTIONS","item_id":"item-abc"}'
        token = jwt.encode(
            {
                "request_body_sha256": hashlib.sha256(body).hexdigest(),
                "iat": int(time.time()),
            },
            private,
            algorithm="ES256",
            headers={"kid": "test-key"},
        )
        with patch("api.plaid_webhook.plaid_post", return_value={"key": jwk}):
            verify_plaid_webhook(body, token)
            with self.assertRaises(PlaidWebhookError):
                verify_plaid_webhook(body + b"x", token)

    def test_webhook_rejects_bad_signature(self):
        app = FastAPI()
        app.include_router(plaid_webhook_router)
        client = TestClient(app)
        response = client.post(
            "/api/plaid/webhook",
            content=b"{}",
            headers={"Plaid-Verification": "not-a-jwt"},
        )
        self.assertEqual(response.status_code, 401)

    def test_verified_webhook_starts_sync(self):
        app = FastAPI()
        app.include_router(plaid_webhook_router)
        client = TestClient(app)
        with (
            patch("api.routes.plaid_webhook.verify_plaid_webhook"),
            patch("api.routes.plaid_webhook.sync_for_webhook") as sync,
        ):
            response = client.post(
                "/api/plaid/webhook",
                json={
                    "webhook_type": "TRANSACTIONS",
                    "webhook_code": "SYNC_UPDATES_AVAILABLE",
                    "item_id": "item-abc",
                },
                headers={"Plaid-Verification": "signed"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(sync.call_args.args[0], "item-abc")
        self.assertIsInstance(sync.call_args.args[1], datetime)


if __name__ == "__main__":
    unittest.main()
