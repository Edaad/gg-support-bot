"""QuickNode webhook receiver: HMAC, timestamp window, hint extraction, storage."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.quicknode_webhook import router
from bot.services.quicknode.hints import extract_hints, tron_dust_hashes
from db.models import QuicknodeTxHint
from tests.support import quicknode as f
from tests.support.sqlite_db import get_db_for, make_sqlite_session_factory

SECRET = "qn-test-secret"
ETH_TX = "0x" + "ab" * 32


def _sign(
    body: bytes, *, secret: str = SECRET, nonce: str = "n1", ts: int | None = None
):
    ts_str = str(int(time.time()) if ts is None else ts)
    sig = hmac.new(
        secret.encode(), nonce.encode() + ts_str.encode() + body, hashlib.sha256
    ).hexdigest()
    return {"x-qn-nonce": nonce, "x-qn-timestamp": ts_str, "x-qn-signature": sig}


class QuicknodeWebhookTestCase(unittest.TestCase):
    def setUp(self):
        self.factory = make_sqlite_session_factory(["quicknode_tx_hints"])
        self.patches = [
            patch("bot.services.quicknode.store.get_db", get_db_for(self.factory)),
            patch.dict(os.environ, {"QUICKNODE_WEBHOOK_SECRET": f"older,{SECRET}"}),
        ]
        for p in self.patches:
            p.start()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def post(self, chain: str, payload, headers=None, raw: bytes | None = None):
        body = raw if raw is not None else json.dumps(payload).encode()
        return self.client.post(
            f"/api/crypto/quicknode/webhook?chain={chain}",
            content=body,
            headers=headers if headers is not None else _sign(body),
        )

    def hints(self):
        with self.factory() as s:
            return [
                (h.chain, h.tx_hash, h.state) for h in s.query(QuicknodeTxHint).all()
            ]

    def test_valid_delivery_stores_pending_hint(self):
        resp = self.post(
            "ethereum", {"matchingReceipts": [{"transactionHash": ETH_TX}]}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["new"], 1)
        self.assertEqual(self.hints(), [("ethereum", ETH_TX, "pending")])

    def test_bad_hmac_stores_nothing(self):
        body = json.dumps({"hash": ETH_TX}).encode()
        headers = _sign(body, secret="wrong")
        self.assertEqual(self.post("ethereum", None, headers, body).status_code, 401)
        tampered = json.dumps({"hash": "0x" + "cd" * 32}).encode()
        self.assertEqual(
            self.post("ethereum", None, _sign(body), tampered).status_code, 401
        )
        self.assertEqual(self.hints(), [])

    def test_missing_headers(self):
        body = json.dumps({"hash": ETH_TX}).encode()
        headers = _sign(body)
        del headers["x-qn-nonce"]
        self.assertEqual(self.post("ethereum", None, headers, body).status_code, 401)
        self.assertEqual(self.post("ethereum", None, {}, body).status_code, 401)
        self.assertEqual(self.hints(), [])

    def test_stale_timestamp(self):
        body = json.dumps({"hash": ETH_TX}).encode()
        for ts in (int(time.time()) - 301, int(time.time()) + 301):
            resp = self.post("ethereum", None, _sign(body, ts=ts), body)
            self.assertEqual(resp.status_code, 401)
        self.assertEqual(self.hints(), [])

    def test_rotated_secret_accepted(self):
        body = json.dumps({"hash": ETH_TX}).encode()
        resp = self.post("ethereum", None, _sign(body, secret="older"), body)
        self.assertEqual(resp.status_code, 200)

    def test_no_secret_configured_refuses(self):
        with patch.dict(os.environ, {"QUICKNODE_WEBHOOK_SECRET": ""}):
            self.assertEqual(self.post("ethereum", {"hash": ETH_TX}).status_code, 401)

    def test_unknown_chain(self):
        self.assertEqual(self.post("dogecoin", {"hash": ETH_TX}).status_code, 400)

    def test_duplicate_hint_is_ignored(self):
        payload = {
            "a": [
                {"hash": ETH_TX},
                {"transactionHash": ETH_TX.upper().replace("0X", "0x")},
            ]
        }
        self.assertEqual(self.post("ethereum", payload).json()["hints"], 1)
        self.assertEqual(self.post("ethereum", payload).json()["new"], 0)
        self.assertEqual(len(self.hints()), 1)

    def test_tron_dust_pre_dropped(self):
        payload = {
            "matchingTransactions": [
                {"hash": "0x" + f.TRON_DUST_TX, "input": "0x", "value": "0x1"},
                {"hash": "0x" + f.TRON_USDT_TX, "input": "0xa9059cbb", "value": "0x0"},
            ],
            "matchingReceipts": [{"transactionHash": "0x" + f.TRON_USDT_TX}],
        }
        resp = self.post("tron", payload)
        self.assertEqual(resp.json()["hints"], 1)
        self.assertEqual(self.hints(), [("tron", f.TRON_USDT_TX, "pending")])

    def test_payload_amounts_are_not_stored(self):
        payload = {"hash": ETH_TX, "value": "0xffffffffffff", "amount": "999999"}
        self.post("ethereum", payload)
        with self.factory() as s:
            row = s.query(QuicknodeTxHint).one()
            self.assertEqual(
                {c.name for c in QuicknodeTxHint.__table__.columns},
                {
                    "id",
                    "chain",
                    "tx_hash",
                    "state",
                    "reason",
                    "attempts",
                    "first_seen_at",
                    "next_check_at",
                    "claimed_at",
                    "alerted_at",
                },
            )
            self.assertEqual(row.tx_hash, ETH_TX)


class HintExtractionTestCase(unittest.TestCase):
    def test_formats_per_chain(self):
        sol_sig = f.solana_sig("usdc")
        payload = {
            "x": [
                {"hash": "0x" + "1" * 64},
                {"transactionHash": "0x" + "2" * 63},  # too short
                {"txid": "3" * 64},
                {"signature": sol_sig},
                {"signatures": [sol_sig, "0OIl" * 20]},
                {"blockHash": "0x" + "4" * 64},  # not a tx key
            ]
        }
        self.assertEqual(extract_hints("ethereum", payload), ["0x" + "1" * 64])
        # Tron accepts 0x-prefixed and bare hex, stored without 0x.
        self.assertEqual(set(extract_hints("tron", payload)), {"1" * 64, "3" * 64})
        self.assertEqual(extract_hints("bitcoin", payload), ["3" * 64])
        self.assertEqual(extract_hints("solana", payload), [sol_sig])

    def test_tron_dust_needs_no_receipt(self):
        dust = {"hash": "0x" + f.TRON_DUST_TX, "input": "0x", "value": 1}
        self.assertEqual(
            tron_dust_hashes({"matchingTransactions": [dust]}), {f.TRON_DUST_TX}
        )
        self.assertEqual(
            tron_dust_hashes(
                {
                    "matchingTransactions": [dust],
                    "matchingReceipts": [{"transactionHash": "0x" + f.TRON_DUST_TX}],
                }
            ),
            set(),
        )
        big = dict(dust, value="0xf4240")  # exactly 1 TRX is not dust
        self.assertEqual(tron_dust_hashes({"matchingTransactions": [big]}), set())


if __name__ == "__main__":
    unittest.main()
