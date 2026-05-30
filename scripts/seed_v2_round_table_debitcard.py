#!/usr/bin/env python3
"""Seed Round Table deposit Debit Card into greenfield club_payment_* tables.

Idempotent upserts by (club_id, direction, slug) and (method_id, tier label).
Legacy had no tier row; V2 stores one Default tier seeded from method-level data.

Usage (dry run — no writes):
    python scripts/seed_v2_round_table_debitcard.py

Apply + verify via /api/v2:
    python scripts/seed_v2_round_table_debitcard.py --apply
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

from db.connection import get_session
from db.models import Club, ClubPaymentMethod, ClubPaymentTier, ClubPaymentTierVariant

from api.payment_v2_helpers import upsert_default_variant_for_tier as upsert_default_variant

CLUB_NAME = "Round Table"
METHOD_SLUG = "debitcard"
DIRECTION = "deposit"
DEFAULT_TIER_LABEL = "Default"
ACCUMULATED_AMOUNT = Decimal("5894.00")

DEBITCARD_RESPONSE_TEXT = """🚨 NO CREDIT CARDS. They will be refunded immediately

• Enter your deposit amount on the checkout page ($20 minimum, $100 maximum).

• Once sent, please inform us, and an agent will confirm the transaction and add your chips within 2 minutes!

• Just post a screenshot of your transaction, and it will be credited to your account!

{{hyperlink}}"""


def find_round_table_club(session: Session) -> Club:
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

    method.name = "Debit Card"
    method.min_amount = Decimal("20")
    method.max_amount = Decimal("100")
    method.has_sub_options = False
    method.is_active = True
    method.sort_order = 3
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

    tier.min_amount = Decimal("20")
    tier.max_amount = Decimal("100")
    tier.sort_order = 0
    tier.response_type = "text"
    tier.response_text = None
    tier.response_file_id = None
    tier.response_caption = None
    tier.use_group_checkout_link = True
    tier.group_checkout_provider = "stripe"
    tier.hyperlink_text = "PAY HERE"
    tier.checkout_min_amount = Decimal("20")
    tier.checkout_max_amount = Decimal("100")
    session.flush()
    return tier


def seed(session: Session) -> tuple[Club, ClubPaymentMethod, ClubPaymentTier, ClubPaymentTierVariant]:
    club = find_round_table_club(session)
    method = upsert_method(session, club.id)
    tier = upsert_default_tier(session, method.id)
    variant = upsert_default_variant(session, tier, response_text=DEBITCARD_RESPONSE_TEXT)
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
    debitcard = [m for m in methods if m.get("slug") == METHOD_SLUG]
    if len(debitcard) != 1:
        raise SystemExit(f"Expected exactly one debitcard method, got {len(debitcard)}")

    method = debitcard[0]
    if method.get("has_sub_options"):
        raise SystemExit("Expected has_sub_options=false on Debit Card method")

    min_amount = method.get("min_amount")
    max_amount = method.get("max_amount")
    if Decimal(str(min_amount)) != Decimal("20") or Decimal(str(max_amount)) != Decimal("100"):
        raise SystemExit(f"Expected method min=20 max=100, got min={min_amount!r} max={max_amount!r}")

    subs = method.get("sub_options") or []
    if subs:
        raise SystemExit(f"Expected 0 sub-options, got {len(subs)}")

    tiers = method.get("tiers") or []
    if len(tiers) != 1:
        raise SystemExit(f"Expected 1 tier, got {len(tiers)}")

    tier = tiers[0]
    if tier.get("label") != DEFAULT_TIER_LABEL:
        raise SystemExit(f"Expected tier label {DEFAULT_TIER_LABEL!r}, got {tier.get('label')!r}")

    if Decimal(str(tier.get("min_amount"))) != Decimal("20"):
        raise SystemExit(f"Expected tier min_amount 20, got {tier.get('min_amount')!r}")
    if Decimal(str(tier.get("max_amount"))) != Decimal("100"):
        raise SystemExit(f"Expected tier max_amount 100, got {tier.get('max_amount')!r}")

    if (tier.get("response_text") or "").strip():
        raise SystemExit("Expected empty tier response_text; copy lives on variant")

    if not tier.get("use_group_checkout_link"):
        raise SystemExit("Expected use_group_checkout_link=true on tier")
    if tier.get("group_checkout_provider") != "stripe":
        raise SystemExit(f"Expected group_checkout_provider=stripe, got {tier.get('group_checkout_provider')!r}")
    if tier.get("hyperlink_text") != "PAY HERE":
        raise SystemExit(f"Expected hyperlink_text='PAY HERE', got {tier.get('hyperlink_text')!r}")

    checkout_min = tier.get("checkout_min_amount")
    checkout_max = tier.get("checkout_max_amount")
    if Decimal(str(checkout_min)) != Decimal("20") or Decimal(str(checkout_max)) != Decimal("100"):
        raise SystemExit(
            f"Expected checkout min=20 max=100, got min={checkout_min!r} max={checkout_max!r}"
        )

    variants = tier.get("variants") or []
    if len(variants) != 1:
        raise SystemExit(f"Expected 1 variant on tier, got {len(variants)}")
    if "{{hyperlink}}" not in (variants[0].get("response_text") or ""):
        raise SystemExit("Expected {{hyperlink}} placeholder in variant response_text")

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
                f"Seeded Round Table Debit Card (v2): club_id={club.id}, method_id={method.id}, "
                f"tier_id={tier.id}, variant_id={variant.id}"
            )
            verify_via_api(club.id)
            print(
                "API verification passed: 1 Default tier ($20–$100), 1 Default variant, "
                "Stripe checkout, 0 sub-options."
            )
        else:
            session.rollback()
            print("Dry run (no changes committed). Would upsert:")
            print(f"  club: {club.name!r} (id={club.id})")
            print(
                f"  method: Debit Card / {METHOD_SLUG} (deposit, $20–$100, "
                f"accumulated=${ACCUMULATED_AMOUNT:,.2f}, sort_order=3)"
            )
            print(f"  tier: {DEFAULT_TIER_LABEL!r} (Stripe defaults on tier)")
            print(f"  variant: {DEFAULT_TIER_LABEL!r} ({{hyperlink}}, PAY HERE)")
            print("  sub-options: 0")
            print("Re-run with --apply to commit and verify.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
