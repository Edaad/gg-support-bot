#!/usr/bin/env python3
"""Seed ClubGTO deposit Zelle into greenfield club_payment_* tables.

Idempotent upserts by (club_id, direction, slug), (method_id, tier label),
and (tier_id, variant label). Two tiers; player copy on Default variants only.

Usage (dry run — no writes):
    python scripts/seed_v2_clubgto_zelle.py

Apply + verify via /api/v2:
    python scripts/seed_v2_clubgto_zelle.py --apply
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from sqlalchemy.orm import Session

from api.payment_v2_helpers import clear_tier_response
from api.payment_v2_helpers import upsert_default_variant_for_tier as upsert_default_variant
from db.connection import get_session
from db.models import Club, ClubPaymentMethod, ClubPaymentTier, ClubPaymentTierVariant

CLUB_NAME = "ClubGTO"
METHOD_SLUG = "zelle"
DIRECTION = "deposit"
TIER_UNDER_LABEL = "Under 399"
TIER_OVER_LABEL = "Over $400"
DEFAULT_VARIANT_LABEL = "Default"

ZELLE_RESPONSE_TEXT = """Zelle: 310-567-0961

• Please put a random emoji in the payment caption when sending

• Once sent, please inform us, and an agent will confirm the transaction and add your chips within 2 minutes!"""


def find_clubgto_club(session: Session) -> Club:
    matches = session.query(Club).filter(Club.name == CLUB_NAME).all()
    if not matches:
        raise SystemExit(f"Club not found: {CLUB_NAME!r}")
    if len(matches) > 1:
        ids = [c.id for c in matches]
        raise SystemExit(f"Multiple clubs named {CLUB_NAME!r}: ids={ids}")
    return matches[0]


def upsert_method(session: Session, club_id: int) -> ClubPaymentMethod:
    method = (
        session.query(ClubPaymentMethod)
        .filter_by(club_id=club_id, direction=DIRECTION, slug=METHOD_SLUG)
        .first()
    )
    if method is None:
        method = ClubPaymentMethod(club_id=club_id, direction=DIRECTION, slug=METHOD_SLUG)
        session.add(method)

    method.name = "Zelle"
    method.min_amount = Decimal("20")
    method.max_amount = None
    method.has_sub_options = False
    method.is_active = True
    method.sort_order = 1
    method.deposit_limit = None
    method.accumulated_amount = None
    session.flush()
    return method


def upsert_under_tier(session: Session, method_id: int) -> ClubPaymentTier:
    tier = (
        session.query(ClubPaymentTier)
        .filter_by(method_id=method_id, label=TIER_UNDER_LABEL)
        .first()
    )
    if tier is None:
        tier = ClubPaymentTier(method_id=method_id, label=TIER_UNDER_LABEL)
        session.add(tier)

    tier.min_amount = Decimal("20")
    tier.max_amount = Decimal("399")
    tier.sort_order = 0
    tier.response_type = "text"
    clear_tier_response(tier)
    tier.use_group_checkout_link = False
    tier.group_checkout_provider = None
    tier.hyperlink_text = None
    tier.checkout_min_amount = None
    tier.checkout_max_amount = None
    session.flush()
    return tier


def upsert_over_tier(session: Session, method_id: int) -> ClubPaymentTier:
    tier = (
        session.query(ClubPaymentTier)
        .filter_by(method_id=method_id, label=TIER_OVER_LABEL)
        .first()
    )
    if tier is None:
        tier = ClubPaymentTier(method_id=method_id, label=TIER_OVER_LABEL)
        session.add(tier)

    tier.min_amount = Decimal("400")
    tier.max_amount = None
    tier.sort_order = 1
    tier.response_type = "text"
    clear_tier_response(tier)
    tier.use_group_checkout_link = False
    tier.group_checkout_provider = None
    tier.hyperlink_text = None
    tier.checkout_min_amount = None
    tier.checkout_max_amount = None
    session.flush()
    return tier


def seed(
    session: Session,
) -> tuple[Club, ClubPaymentMethod, list[ClubPaymentTier], list[ClubPaymentTierVariant]]:
    club = find_clubgto_club(session)
    method = upsert_method(session, club.id)
    tier_under = upsert_under_tier(session, method.id)
    tier_over = upsert_over_tier(session, method.id)

    variants = []
    for tier in (tier_under, tier_over):
        variant = upsert_default_variant(session, tier, response_text=ZELLE_RESPONSE_TEXT)
        variant.weight = 100
        variant.sort_order = 0
        variants.append(variant)

    session.flush()
    return club, method, [tier_under, tier_over], variants


def _tier_by_label(tiers: list[dict], label: str) -> dict:
    matches = [t for t in tiers if t.get("label") == label]
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one tier {label!r}, got {len(matches)}")
    return matches[0]


def verify_via_api(club_id: int) -> None:
    from fastapi.testclient import TestClient

    from api.app import create_app
    from api.auth import create_token

    client = TestClient(create_app())
    token = create_token()
    resp = client.get(
        f"/api/v2/clubs/{club_id}/methods?direction=deposit",
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 200:
        raise SystemExit(f"API verify failed: HTTP {resp.status_code} — {resp.text}")

    methods = resp.json()
    zelle = [m for m in methods if m.get("slug") == METHOD_SLUG]
    if len(zelle) != 1:
        raise SystemExit(f"Expected exactly one zelle method, got {len(zelle)}")

    method = zelle[0]
    if method.get("has_sub_options"):
        raise SystemExit("Expected has_sub_options=false on Zelle method")

    if Decimal(str(method.get("min_amount"))) != Decimal("20"):
        raise SystemExit(f"Expected method min_amount 20, got {method.get('min_amount')!r}")
    if method.get("max_amount") is not None:
        raise SystemExit(f"Expected method max_amount NULL, got {method.get('max_amount')!r}")
    if method.get("sort_order") != 1:
        raise SystemExit(f"Expected sort_order 1, got {method.get('sort_order')!r}")

    accumulated = method.get("accumulated_amount")
    if accumulated is not None and Decimal(str(accumulated)) != Decimal("0"):
        raise SystemExit(f"Expected accumulated_amount null/0, got {accumulated!r}")

    subs = method.get("sub_options") or []
    if subs:
        raise SystemExit(f"Expected 0 sub-options, got {len(subs)}")

    tiers = method.get("tiers") or []
    if len(tiers) != 2:
        raise SystemExit(f"Expected 2 tiers, got {len(tiers)}")

    tier_under = _tier_by_label(tiers, TIER_UNDER_LABEL)
    tier_over = _tier_by_label(tiers, TIER_OVER_LABEL)

    for tier in (tier_under, tier_over):
        if (tier.get("response_text") or "").strip():
            raise SystemExit(f"Expected empty tier response_text on {tier.get('label')!r}")
        if tier.get("use_group_checkout_link"):
            raise SystemExit(f"Expected use_group_checkout_link=false on {tier.get('label')!r}")

    if Decimal(str(tier_under.get("min_amount"))) != Decimal("20"):
        raise SystemExit(f"Expected Under tier min 20, got {tier_under.get('min_amount')!r}")
    if Decimal(str(tier_under.get("max_amount"))) != Decimal("399"):
        raise SystemExit(f"Expected Under tier max 399, got {tier_under.get('max_amount')!r}")
    if Decimal(str(tier_over.get("min_amount"))) != Decimal("400"):
        raise SystemExit(f"Expected Over tier min 400, got {tier_over.get('min_amount')!r}")
    if tier_over.get("max_amount") is not None:
        raise SystemExit(f"Expected Over tier max_amount NULL, got {tier_over.get('max_amount')!r}")

    for tier in (tier_under, tier_over):
        variants = tier.get("variants") or []
        if len(variants) != 1:
            raise SystemExit(
                f"Expected 1 variant on {tier.get('label')!r}, got {len(variants)}"
            )
        variant = variants[0]
        if variant.get("label") != DEFAULT_VARIANT_LABEL:
            raise SystemExit(f"Expected variant {DEFAULT_VARIANT_LABEL!r}")
        if variant.get("weight") != 100:
            raise SystemExit(f"Expected variant weight 100, got {variant.get('weight')!r}")
        text = variant.get("response_text") or ""
        if not text.strip():
            raise SystemExit(f"Expected non-empty response_text on {tier.get('label')!r}")
        if "310-567-0961" not in text:
            raise SystemExit("Expected 310-567-0961 in Default variant response_text")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write to database and verify via GET /api/v2/...",
    )
    args = parser.parse_args()

    from migrate_club_payment_v2 import main as ensure_v2_tables

    ensure_v2_tables()

    session = get_session()
    try:
        club, method, tiers, variants = seed(session)
        if args.apply:
            session.commit()
            print(
                f"Seeded ClubGTO Zelle (v2): club_id={club.id}, method_id={method.id}, "
                f"tier_ids={[t.id for t in tiers]}, variant_ids={[v.id for v in variants]}"
            )
            verify_via_api(club.id)
            print(
                "API verification passed: 2 tiers (Under 399, Over $400), "
                "2 Default variants (weight 100), 0 sub-options."
            )
        else:
            session.rollback()
            print("Dry run (no changes committed). Would upsert:")
            print(f"  club: {club.name!r} (id={club.id})")
            print(
                f"  method: Zelle / {METHOD_SLUG} (deposit, min=$20, "
                f"accumulated=null, sort_order=1)"
            )
            print(f"  tier: {TIER_UNDER_LABEL!r} ($20–$399, amount band only)")
            print(f"  tier: {TIER_OVER_LABEL!r} ($400+, amount band only)")
            print(f"  variants: 2x {DEFAULT_VARIANT_LABEL!r} (weight=100, Zelle instructions)")
            print("  sub-options: 0")
            print("Re-run with --apply to commit and verify.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
