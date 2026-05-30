#!/usr/bin/env python3
"""Seed Creator Club deposit Zelle into greenfield club_payment_* tables.

Idempotent upserts by (club_id, direction, slug) and (method_id, tier label).
Legacy stored Zelle copy on the method row; V2 moves it to a Default variant.

Usage (dry run — no writes):
    python scripts/seed_v2_creator_club_zelle.py

Apply + verify via /api/v2:
    python scripts/seed_v2_creator_club_zelle.py --apply
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

from api.payment_v2_helpers import upsert_default_variant_for_tier as upsert_default_variant
from db.connection import get_session
from db.models import Club, ClubPaymentMethod, ClubPaymentTier, ClubPaymentTierVariant

CLUB_NAME = "Creator Club"
METHOD_SLUG = "zelle"
DIRECTION = "deposit"
DEFAULT_TIER_LABEL = "Default"
ACCUMULATED_AMOUNT = Decimal("26870.00")

ZELLE_RESPONSE_TEXT = """Zelle Email: coachingg444@gmail.com
Zelle Name: CONCORD CONSULTING AGENCY, INC
• Please put a random emoji in the payment caption when sending
• Once sent, please inform us, and an agent will confirm the transaction and add your chips within 2 minutes!

You can send payment to this address anytime in the future. Just post a screenshot of your latest transaction, and it will be credited to your account!"""


def find_creator_club(session: Session) -> Club:
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
    method.min_amount = Decimal("50")
    method.max_amount = None
    method.has_sub_options = False
    method.is_active = True
    method.sort_order = 1
    method.deposit_limit = None
    method.accumulated_amount = ACCUMULATED_AMOUNT
    session.flush()
    return method


def upsert_default_tier(session: Session, method_id: int) -> ClubPaymentTier:
    tier = (
        session.query(ClubPaymentTier)
        .filter_by(method_id=method_id, label=DEFAULT_TIER_LABEL)
        .first()
    )
    if tier is None:
        tier = ClubPaymentTier(method_id=method_id, label=DEFAULT_TIER_LABEL)
        session.add(tier)

    tier.min_amount = Decimal("50")
    tier.max_amount = None
    tier.sort_order = 0
    tier.response_type = "text"
    tier.response_text = None
    tier.response_file_id = None
    tier.response_caption = None
    tier.use_group_checkout_link = False
    tier.group_checkout_provider = None
    tier.hyperlink_text = None
    tier.checkout_min_amount = None
    tier.checkout_max_amount = None
    session.flush()
    return tier


def seed(session: Session) -> tuple[Club, ClubPaymentMethod, ClubPaymentTier, ClubPaymentTierVariant]:
    club = find_creator_club(session)
    method = upsert_method(session, club.id)
    tier = upsert_default_tier(session, method.id)
    variant = upsert_default_variant(session, tier, response_text=ZELLE_RESPONSE_TEXT)
    variant.weight = 100
    session.flush()
    return club, method, tier, variant


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

    if Decimal(str(method.get("min_amount"))) != Decimal("50"):
        raise SystemExit(f"Expected method min_amount 50, got {method.get('min_amount')!r}")

    subs = method.get("sub_options") or []
    if subs:
        raise SystemExit(f"Expected 0 sub-options, got {len(subs)}")

    tiers = method.get("tiers") or []
    if len(tiers) != 1:
        raise SystemExit(f"Expected 1 tier, got {len(tiers)}")

    tier = tiers[0]
    if tier.get("label") != DEFAULT_TIER_LABEL:
        raise SystemExit(f"Expected tier label {DEFAULT_TIER_LABEL!r}, got {tier.get('label')!r}")

    min_amount = tier.get("min_amount")
    if min_amount is None or Decimal(str(min_amount)) != Decimal("50"):
        raise SystemExit(f"Expected tier min_amount 50, got {min_amount!r}")

    if (tier.get("response_text") or "").strip():
        raise SystemExit("Expected empty tier response_text; copy lives on variant")

    variants = tier.get("variants") or []
    if len(variants) != 1:
        raise SystemExit(f"Expected 1 variant on tier, got {len(variants)}")
    if variants[0].get("label") != DEFAULT_TIER_LABEL:
        raise SystemExit(f"Expected variant label {DEFAULT_TIER_LABEL!r}")
    if variants[0].get("weight") != 100:
        raise SystemExit(f"Expected variant weight 100, got {variants[0].get('weight')!r}")

    response_text = variants[0].get("response_text") or ""
    if not response_text.strip():
        raise SystemExit("Expected non-empty response_text on Default variant")
    if "coachingg444@gmail.com" not in response_text:
        raise SystemExit("Expected coachingg444@gmail.com in Default variant response_text")

    accumulated = method.get("accumulated_amount")
    if Decimal(str(accumulated)) != ACCUMULATED_AMOUNT:
        raise SystemExit(
            f"Expected accumulated_amount={ACCUMULATED_AMOUNT}, got {accumulated!r}"
        )


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
        club, method, tier, variant = seed(session)
        if args.apply:
            session.commit()
            print(
                f"Seeded Creator Club Zelle (v2): club_id={club.id}, method_id={method.id}, "
                f"tier_id={tier.id}, variant_id={variant.id}"
            )
            verify_via_api(club.id)
            print("API verification passed: 1 Default tier, 1 Default variant, 0 sub-options.")
        else:
            session.rollback()
            print("Dry run (no changes committed). Would upsert:")
            print(f"  club: {club.name!r} (id={club.id})")
            print(
                f"  method: Zelle / {METHOD_SLUG} (deposit, min=$50, "
                f"accumulated=${ACCUMULATED_AMOUNT:,.2f}, sort_order=1)"
            )
            print(f"  tier: {DEFAULT_TIER_LABEL!r} (min=$50, amount band only)")
            print(f"  variant: {DEFAULT_TIER_LABEL!r} (weight=100, Zelle instructions)")
            print("  sub-options: 0")
            print("Re-run with --apply to commit and verify.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
