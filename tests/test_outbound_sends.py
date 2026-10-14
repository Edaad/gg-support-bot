"""Outbound Venmo and Cash App send ingest and list."""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.auth import create_token
from api.routes.outbound_sends import WEBHOOK_SECRET_ENV, router
from api.outbound_sends import (
    _flush_or_duplicate,
    create_outbound_send,
    delete_outbound_send,
    ingest_outbound_send,
    list_outbound_sends,
    update_outbound_send,
)
from db.models import (
    Base,
    Club,
    ClubPaymentMethod,
    ClubPaymentTier,
    ClubPaymentTierVariant,
    OutboundSend,
)

WEBHOOK_SECRET = "test-outbound-secret"


class OutboundSendServiceTestCase(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        tables = [
            Club.__table__,
            ClubPaymentMethod.__table__,
            ClubPaymentTier.__table__,
            ClubPaymentTierVariant.__table__,
            Base.metadata.tables["outbound_sends"],
        ]
        Base.metadata.create_all(engine, tables=tables)
        self.session = sessionmaker(bind=engine)()
        club = Club(name="Round Table", telegram_user_id=1)
        self.session.add(club)
        self.session.flush()
        method = ClubPaymentMethod(
            club_id=club.id,
            direction="deposit",
            name="Venmo",
            slug="venmo",
        )
        self.session.add(method)
        self.session.flush()
        tier = ClubPaymentTier(method_id=method.id, label="Default")
        self.session.add(tier)
        self.session.flush()
        self.session.add(
            ClubPaymentTierVariant(
                method_id=method.id,
                tier_id=tier.id,
                label="Venmo 3",
                venmo_tag="@jagger4444",
            )
        )
        self.session.commit()

    def tearDown(self):
        self.session.close()

    def test_normalizes_tag_and_matches(self):
        result = ingest_outbound_send(
            self.session,
            method="Venmo",
            tag="Jagger4444",
            method_owner="round-table",
            recipient="Player One",
            amount="50.25",
            source_external_id="zap-1",
            paid_at="2026-09-24T10:00:00Z",
        )
        self.session.commit()
        self.assertTrue(result.created)
        self.assertTrue(result.tag_matched)
        self.assertIsNone(result.warning)
        row = list_outbound_sends(self.session)[0][0]
        self.assertEqual(row.tag, "@jagger4444")
        self.assertEqual(row.amount_cents, 5025)

    def test_crypto_keeps_wallet_tag_unmatched(self):
        result = ingest_outbound_send(
            self.session,
            method="crypto",
            tag="0xc50b01cAa68EcB657807b198E1C3360fAC3CBA96",
            method_owner="round-table",
            recipient="0x1C9d6017Ca7c9EF73a29E895325333Da1b418244",
            amount="56.01",
            source_external_id="tx-1",
            paid_at="2026-09-27T18:54:47Z",
        )
        self.session.commit()
        self.assertTrue(result.created)
        self.assertFalse(result.tag_matched)
        row = list_outbound_sends(self.session, method="crypto")[0][0]
        self.assertEqual(row.tag, "0xc50b01cAa68EcB657807b198E1C3360fAC3CBA96")
        self.assertEqual(row.amount_cents, 5601)
        self.assertEqual(row.method, "crypto")

    def test_unknown_tag_still_saves(self):
        result = ingest_outbound_send(
            self.session,
            method="venmo",
            tag="@unknown",
            method_owner="vaughn",
            recipient="Player Two",
            amount=10,
            source_external_id="zap-2",
        )
        self.assertTrue(result.created)
        self.assertFalse(result.tag_matched)
        self.assertEqual(result.warning, "no matching tag found")

    def test_repeat_returns_original(self):
        first = ingest_outbound_send(
            self.session,
            method="venmo",
            tag="@jagger4444",
            method_owner="round-table",
            recipient="Player One",
            amount=20,
            source_external_id="zap-3",
        )
        self.session.commit()
        second = ingest_outbound_send(
            self.session,
            method="venmo",
            tag="@other",
            method_owner="mateos",
            recipient="Someone Else",
            amount=99,
            source_external_id="zap-3",
        )
        self.assertFalse(second.created)
        self.assertEqual(second.id, first.id)
        self.assertTrue(second.tag_matched)
        items, total = list_outbound_sends(self.session)
        self.assertEqual(total, 1)
        self.assertEqual(items[0].recipient, "Player One")
        self.assertEqual(items[0].amount_cents, 2000)

    def test_rejects_other_method_and_nonpositive_amount(self):
        with self.assertRaises(ValueError):
            ingest_outbound_send(
                self.session,
                method="zelle",
                tag="x",
                method_owner="round-table",
                recipient="Player",
                amount=10,
                source_external_id="zap-4",
            )
        with self.assertRaises(ValueError):
            ingest_outbound_send(
                self.session,
                method="venmo",
                tag="@jagger4444",
                method_owner="round-table",
                recipient="Player",
                amount=0,
                source_external_id="zap-5",
            )

    def test_list_filters_method_tag_and_created_at(self):
        ingest_outbound_send(
            self.session,
            method="venmo",
            tag="@jagger4444",
            method_owner="round-table",
            recipient="A",
            amount=5,
            source_external_id="zap-6",
        )
        self.session.commit()
        start = datetime.now(timezone.utc)
        items, total = list_outbound_sends(
            self.session,
            method="venmo",
            tag="jagger4444",
            from_dt=start.replace(year=start.year - 1),
            to_dt=start.replace(year=start.year + 1),
        )
        self.assertEqual(total, 1)
        self.assertEqual(items[0].tag, "@jagger4444")
        empty, empty_total = list_outbound_sends(self.session, method="cashapp")
        self.assertEqual(empty_total, 0)
        self.assertEqual(empty, [])
        prefix, prefix_total = list_outbound_sends(self.session, tag="$")
        self.assertEqual(prefix_total, total)
        self.assertEqual(len(prefix), len(items))

    def test_list_orders_and_filters_by_paid_at(self):
        later = ingest_outbound_send(
            self.session,
            method="venmo",
            tag="@jagger4444",
            method_owner="round-table",
            recipient="Later paid",
            amount=5,
            source_external_id="paid-later",
            paid_at="2026-10-15T02:00:00Z",
        )
        earlier = ingest_outbound_send(
            self.session,
            method="venmo",
            tag="@jagger4444",
            method_owner="round-table",
            recipient="Earlier paid",
            amount=5,
            source_external_id="paid-earlier",
            paid_at="Wed, 14 Oct 2026 20:33:19 +0000",
        )
        self.session.commit()
        later_row = self.session.get(OutboundSend, later.id)
        earlier_row = self.session.get(OutboundSend, earlier.id)
        assert later_row is not None and earlier_row is not None
        later_row.created_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
        earlier_row.created_at = datetime(2026, 10, 16, tzinfo=timezone.utc)
        self.session.commit()

        items, total = list_outbound_sends(self.session)
        self.assertEqual(total, 2)
        self.assertEqual(
            [row.recipient for row in items], ["Later paid", "Earlier paid"]
        )
        window, window_total = list_outbound_sends(
            self.session,
            from_dt=datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc),
            to_dt=datetime(2026, 10, 15, 23, 59, tzinfo=timezone.utc),
        )
        self.assertEqual(window_total, 1)
        self.assertEqual(window[0].recipient, "Later paid")

    def test_admin_create_update_delete(self):
        row = create_outbound_send(
            self.session,
            method="venmo",
            tag="Jagger4444",
            method_owner="round-table",
            recipient="Player",
            amount="10",
            source_external_id="admin-1",
        )
        self.session.commit()
        self.assertTrue(row.tag_matched)
        self.assertEqual(row.tag, "@jagger4444")

        with self.assertRaises(ValueError):
            create_outbound_send(
                self.session,
                method="venmo",
                tag="@jagger4444",
                method_owner="round-table",
                recipient="Other",
                amount=5,
                source_external_id="admin-1",
            )

        updated = update_outbound_send(
            self.session,
            int(row.id),
            method="cashapp",
            tag="adamaole",
            method_owner="vaughn",
            recipient="Edited",
            amount="12.50",
            source_external_id="admin-1",
            paid_at="yesterday",
        )
        self.session.commit()
        self.assertEqual(updated.tag, "$adamaole")
        self.assertFalse(updated.tag_matched)
        self.assertEqual(updated.amount_cents, 1250)
        self.assertEqual(updated.recipient, "Edited")
        self.assertIsNotNone(updated.created_at)

        other = create_outbound_send(
            self.session,
            method="venmo",
            tag="@jagger4444",
            method_owner="round-table",
            recipient="Other",
            amount=3,
            source_external_id="admin-2",
        )
        self.session.commit()
        with self.assertRaises(ValueError):
            update_outbound_send(
                self.session,
                int(other.id),
                method="venmo",
                tag="@jagger4444",
                method_owner="round-table",
                recipient="Other",
                amount=3,
                source_external_id="admin-1",
            )

        delete_outbound_send(self.session, int(row.id))
        delete_outbound_send(self.session, int(other.id))
        self.session.commit()
        _items, total = list_outbound_sends(self.session)
        self.assertEqual(total, 0)
        with self.assertRaises(LookupError):
            delete_outbound_send(self.session, int(row.id))

    def test_unique_index_conflict_is_a_value_error(self):
        db = MagicMock()
        db.flush.side_effect = IntegrityError("insert", {}, Exception("dup"))
        with self.assertRaises(ValueError):
            _flush_or_duplicate(db)
        db.rollback.assert_called_once()


class OutboundSendApiTestCase(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(
            os.environ,
            {WEBHOOK_SECRET_ENV: WEBHOOK_SECRET, "DASHBOARD_PASSWORD": "changeme"},
            clear=False,
        )
        self.env_patch.start()
        self.app = FastAPI()
        self.app.include_router(router)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.env_patch.stop()

    def test_ingest_requires_secret(self):
        response = self.client.post(
            "/api/outbound-sends",
            json=_payload(),
        )
        self.assertEqual(response.status_code, 401)

    def test_list_requires_jwt(self):
        response = self.client.get("/api/outbound-sends")
        self.assertIn(response.status_code, (401, 403))

    def test_ingest_and_list(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            engine,
            tables=[
                Club.__table__,
                ClubPaymentMethod.__table__,
                ClubPaymentTier.__table__,
                ClubPaymentTierVariant.__table__,
                Base.metadata.tables["outbound_sends"],
            ],
        )
        session = sessionmaker(bind=engine)()

        @contextmanager
        def fake_db():
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

        with patch("api.routes.outbound_sends.get_db", fake_db):
            created = self.client.post(
                "/api/outbound-sends",
                json=_payload(),
                headers={"X-Outbound-Webhook-Secret": WEBHOOK_SECRET},
            )
            self.assertEqual(created.status_code, 200)
            body = created.json()
            self.assertTrue(body["created"])
            self.assertFalse(body["tag_matched"])
            self.assertEqual(body["warning"], "no matching tag found")

            listed = self.client.get(
                "/api/outbound-sends",
                headers={"Authorization": f"Bearer {create_token()}"},
            )
        self.assertEqual(listed.status_code, 200)
        data = listed.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["tag"], "@jagger4444")
        self.assertEqual(data["items"][0]["amount_cents"], 5000)
        session.close()

    def test_non_admin_cannot_list_or_write(self):
        headers = {"Authorization": f"Bearer {create_token('account_manager')}"}
        self.assertEqual(
            self.client.get("/api/outbound-sends", headers=headers).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/outbound-sends/admin", json=_payload(), headers=headers
            ).status_code,
            403,
        )

    def test_admin_create_update_duplicate_and_delete(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            engine,
            tables=[
                Club.__table__,
                ClubPaymentMethod.__table__,
                ClubPaymentTier.__table__,
                ClubPaymentTierVariant.__table__,
                Base.metadata.tables["outbound_sends"],
            ],
        )
        session = sessionmaker(bind=engine)()

        @contextmanager
        def fake_db():
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

        headers = {"Authorization": f"Bearer {create_token()}"}
        with patch("api.routes.outbound_sends.get_db", fake_db):
            created = self.client.post(
                "/api/outbound-sends/admin",
                json=_payload(),
                headers=headers,
            )
            self.assertEqual(created.status_code, 200)
            send_id = created.json()["id"]
            duplicate = self.client.post(
                "/api/outbound-sends/admin",
                json=_payload(),
                headers=headers,
            )
            self.assertEqual(duplicate.status_code, 400)
            updated = self.client.patch(
                f"/api/outbound-sends/{send_id}",
                json={**_payload(), "recipient": "Edited", "amount": 12},
                headers=headers,
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["recipient"], "Edited")
            self.assertEqual(updated.json()["amount_cents"], 1200)
            missing = self.client.patch(
                "/api/outbound-sends/999",
                json=_payload(),
                headers=headers,
            )
            self.assertEqual(missing.status_code, 404)
            deleted = self.client.delete(
                f"/api/outbound-sends/{send_id}",
                headers=headers,
            )
            self.assertEqual(deleted.status_code, 204)
        session.close()


def _payload() -> dict:
    return {
        "method": "venmo",
        "tag": "jagger4444",
        "method_owner": "round-table",
        "recipient": "Player One",
        "amount": 50,
        "source_external_id": "zap-api-1",
    }


if __name__ == "__main__":
    unittest.main()
