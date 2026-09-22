"""End-to-end v2 tier/variant API behaviour against an in-memory database."""

from __future__ import annotations

import unittest
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.auth import get_current_admin
from api.routes.v2_payment import router
from db.connection import get_db_dependency
from db.models import (
    Base,
    Club,
    ClubPaymentMethod,
    ClubPaymentSubOption,
    ClubPaymentTier,
    ClubPaymentTierVariant,
    ManualDepositRequest,
)

ManualDepositRequest.__table__.c.instruction_telegram_message_ids.type = JSON()

TABLES = [
    Club.__table__,
    ClubPaymentMethod.__table__,
    ClubPaymentSubOption.__table__,
    ClubPaymentTier.__table__,
    ClubPaymentTierVariant.__table__,
    ManualDepositRequest.__table__,
]


class V2TierApiTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine, tables=TABLES)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        self.session.add(Club(id=1, name="Round Table", telegram_user_id=1))
        self.session.commit()

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_admin] = lambda: "admin"

        def override_db():
            try:
                yield self.session
                self.session.commit()
            except Exception:
                self.session.rollback()
                raise

        app.dependency_overrides[get_db_dependency] = override_db
        self.client = TestClient(app)

    def tearDown(self):
        self.session.close()

    def _create_method(self, slug="venmo", min_amount=100, max_amount=None):
        res = self.client.post(
            "/api/v2/clubs/1/methods",
            json={
                "name": slug.title(),
                "slug": slug,
                "direction": "deposit",
                "min_amount": min_amount,
                "max_amount": max_amount,
            },
        )
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()

    def _venmo_variant(self, tier_id, label, tag, weight=1):
        return self.client.post(
            f"/api/v2/tiers/{tier_id}/variants",
            json={
                "label": label,
                "weight": weight,
                "venmo_tag": tag,
                "venmo_link": f"https://venmo.com/u/{tag.lstrip('@')}",
                "venmo_response_mode": "default",
            },
        )

    def test_live_venmo_shape_can_be_saved_and_edited(self):
        """Default 100+ with a $100-9999 tier: the shape that used to deadlock."""
        method = self._create_method()
        default_tier = method["tiers"][0]

        over = self.client.post(
            f"/api/v2/methods/{method['id']}/tiers",
            json={"label": "$100+", "min_amount": 100, "max_amount": 9999},
        )
        self.assertEqual(over.status_code, 201, over.text)

        res = self.client.put(
            f"/api/v2/methods/{method['id']}",
            json={
                "name": "Venmo",
                "slug": "venmo",
                "direction": "deposit",
                "min_amount": 50,
                "max_amount": None,
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        tiers = {t["label"]: t for t in res.json()["tiers"]}
        self.assertEqual(Decimal(tiers["Default"]["min_amount"]), Decimal("50"))
        self.assertEqual(Decimal(tiers["$100+"]["min_amount"]), Decimal("100"))

        renamed = self.client.put(
            f"/api/v2/tiers/{default_tier['id']}", json={"label": "Default"}
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)

    def test_specific_tiers_still_cannot_overlap(self):
        method = self._create_method(slug="zelle", min_amount=20, max_amount=2000)
        self.client.post(
            f"/api/v2/methods/{method['id']}/tiers",
            json={"label": "$100-500", "min_amount": 100, "max_amount": 500},
        )
        clash = self.client.post(
            f"/api/v2/methods/{method['id']}/tiers",
            json={"label": "$400-900", "min_amount": 400, "max_amount": 900},
        )
        self.assertEqual(clash.status_code, 400)
        self.assertIn("overlaps", clash.json()["detail"].lower())

    def test_second_default_tier_rejected(self):
        method = self._create_method(slug="zelle", min_amount=20)
        clash = self.client.post(
            f"/api/v2/methods/{method['id']}/tiers",
            json={"label": "Default", "min_amount": 500},
        )
        self.assertEqual(clash.status_code, 400)
        self.assertIn("already has", clash.json()["detail"])

    def test_fallback_tier_cannot_be_deleted(self):
        method = self._create_method(slug="zelle", min_amount=20)
        default_tier = method["tiers"][0]
        self.client.post(
            f"/api/v2/methods/{method['id']}/tiers",
            json={"label": "$500+", "min_amount": 500},
        )
        res = self.client.delete(f"/api/v2/tiers/{default_tier['id']}")
        self.assertEqual(res.status_code, 400)
        self.assertIn("fallback tier", res.json()["detail"])

    def test_venmo_tier_starts_without_an_unusable_variant(self):
        method = self._create_method()
        self.assertEqual(method["tiers"][0]["variants"], [])

        tier = self.client.post(
            f"/api/v2/methods/{method['id']}/tiers",
            json={"label": "$500+", "min_amount": 500},
        ).json()
        variants = self.client.get(f"/api/v2/tiers/{tier['id']}/variants").json()
        self.assertEqual(variants, [])

        created = self._venmo_variant(tier["id"], "Venmo 1", "@club-round")
        self.assertEqual(created.status_code, 201, created.text)

    def test_non_venmo_tier_still_seeds_a_default_variant(self):
        method = self._create_method(slug="zelle", min_amount=20)
        self.assertEqual(len(method["tiers"][0]["variants"]), 1)

    def test_variant_checkout_min_below_tier_band_rejected(self):
        method = self._create_method(slug="zelle", min_amount=20, max_amount=9999)
        tier = self.client.post(
            f"/api/v2/methods/{method['id']}/tiers",
            json={"label": "$500-1000", "min_amount": 500, "max_amount": 1000},
        ).json()
        res = self.client.post(
            f"/api/v2/tiers/{tier['id']}/variants",
            json={
                "label": "Low checkout",
                "response_text": "pay here",
                "checkout_min_amount": 100,
            },
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("below the tier minimum", res.json()["detail"])

    def test_variant_update_keeps_checkout_link_override(self):
        method = self._create_method(slug="zelle", min_amount=20)
        tier_id = method["tiers"][0]["id"]
        variant = self.client.get(f"/api/v2/tiers/{tier_id}/variants").json()[0]
        self.client.put(
            f"/api/v2/variants/{variant['id']}",
            json={"use_group_checkout_link": False},
        )
        # A later edit that omits the field must not clear it.
        res = self.client.put(
            f"/api/v2/variants/{variant['id']}",
            json={"label": "Renamed", "response_text": "hi"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIs(res.json()["use_group_checkout_link"], False)


if __name__ == "__main__":
    unittest.main()
