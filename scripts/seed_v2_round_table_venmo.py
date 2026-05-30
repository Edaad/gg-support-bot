#!/usr/bin/env python3
"""Seed Round Table deposit Venmo into greenfield club_payment_* tables.

Idempotent upserts by (club_id, direction, slug), (method_id, tier label),
and (tier_id, variant label). One Default tier, four weighted variants — all
player copy on variants.

Usage (dry run — no writes):
    python scripts/seed_v2_round_table_venmo.py

Apply + verify via /api/v2:
    python scripts/seed_v2_round_table_venmo.py --apply
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
from db.connection import get_session
from db.models import Club, ClubPaymentMethod, ClubPaymentTier, ClubPaymentTierVariant

CLUB_NAME = "Round Table"
METHOD_SLUG = "venmo"
DIRECTION = "deposit"
DEFAULT_TIER_LABEL = "Default"
ACCUMULATED_AMOUNT = Decimal("93211.00")

VARIANT_ACCOUNT_2_LABEL = "Venmo Account 2"
VARIANT_VENMO_4_LABEL = "Venmo 4"
VARIANT_VENMO_3_LABEL = "Venmo 3"
VARIANT_ACCOUNT_1_LABEL = "Venmo Account 1"

VENMO_ACCOUNT_2_TEXT = """Venmo: https://venmo.com/u/godfather4444

• Please put a random emoji in the payment caption when sending

• Once sent, please send a screenshot, and an agent will confirm the transaction and add your chips within 2 minutes!"""

VENMO_4_TEXT = """Venmo: https://venmo.com/u/michaelc4444

• Please put a random emoji in the payment caption when sending

• Once sent, please send a screenshot, and an agent will confirm the transaction and add your chips within 2 minutes!"""

VENMO_3_TEXT = """Venmo: https://venmo.com/u/jagger4444

• Please put a random emoji in the payment caption when sending

• Once sent, please send a screenshot, and an agent will confirm the transaction and add your chips within 2 minutes!"""

VENMO_ACCOUNT_1_TEXT = """Venmo: https://venmo.com/u/club-round

• Please put a random emoji in the payment caption when sending

• Once sent, please send a screenshot, and an agent will confirm the transaction and add your chips within 2 minutes!"""

EXPECTED_VARIANTS: dict[str, tuple[int, str, str]] = {
    VARIANT_ACCOUNT_2_LABEL: (35, 0, "https://venmo.com/u/godfather4444"),
    VARIANT_VENMO_4_LABEL: (15, 1, "https://venmo.com/u/michaelc4444"),
    VARIANT_VENMO_3_LABEL: (15, 2, "https://venmo.com/u/jagger4444"),
    VARIANT_ACCOUNT_1_LABEL: (35, 3, "https://venmo.com/u/club-round"),
}


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

    method.name = "Venmo"
    method.min_amount = Decimal("100")
    method.max_amount = None
    method.has_sub_options = False
    method.is_active = True
    method.sort_order = 5
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

    tier.min_amount = Decimal("100")
    tier.max_amount = None
    tier.sort_order = 0
    clear_tier_response(tier)
    tier.use_group_checkout_link = False
    tier.group_checkout_provider = None
    tier.hyperlink_text = None
    tier.checkout_min_amount = None
    tier.checkout_max_amount = None
    session.flush()
    return tier


def upsert_variant(
    session: Session,
    tier: ClubPaymentTier,
    *,
    label: str,
    weight: int,
    sort_order: int,
    response_text: str,
) -> ClubPaymentTierVariant:
    variant = (
        session.query(ClubPaymentTierVariant)
        .filter_by(tier_id=tier.id, label=label)
        .first()
    )
    if variant is None:
        variant = ClubPaymentTierVariant(
            method_id=tier.method_id,
            tier_id=tier.id,
            label=label,
        )
        session.add(variant)

    variant.weight = weight
    variant.sort_order = sort_order
    variant.response_type = "text"
    variant.response_text = response_text
    variant.response_file_id = None
    variant.response_caption = None
    variant.use_group_checkout_link = False
    variant.group_checkout_provider = None
    variant.hyperlink_text = None
    variant.checkout_min_amount = None
    variant.checkout_max_amount = None
    session.flush()
    return variant


def seed(
    session: Session,
) -> tuple[Club, ClubPaymentMethod, ClubPaymentTier, list[ClubPaymentTierVariant]]:
    club = find_round_table_club(session)
    method = upsert_method(session, club.id)
    tier = upsert_default_tier(session, method.id)

    variants = [
        upsert_variant(
            session,
            tier,
            label=VARIANT_ACCOUNT_2_LABEL,
            weight=35,
            sort_order=0,
            response_text=VENMO_ACCOUNT_2_TEXT,
        ),
        upsert_variant(
            session,
            tier,
            label=VARIANT_VENMO_4_LABEL,
            weight=15,
            sort_order=1,
            response_text=VENMO_4_TEXT,
        ),
        upsert_variant(
            session,
            tier,
            label=VARIANT_VENMO_3_LABEL,
            weight=15,
            sort_order=2,
            response_text=VENMO_3_TEXT,
        ),
        upsert_variant(
            session,
            tier,
            label=VARIANT_ACCOUNT_1_LABEL,
            weight=35,
            sort_order=3,
            response_text=VENMO_ACCOUNT_1_TEXT,
        ),
    ]

    return club, method, tier, variants


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
    venmo = [m for m in methods if m.get("slug") == METHOD_SLUG]
    if len(venmo) != 1:
        raise SystemExit(f"Expected exactly one venmo method, got {len(venmo)}")

    method = venmo[0]
    if method.get("has_sub_options"):
        raise SystemExit("Expected has_sub_options=false on Venmo method")

    if Decimal(str(method.get("min_amount"))) != Decimal("100"):
        raise SystemExit(f"Expected method min_amount 100, got {method.get('min_amount')!r}")
    if method.get("max_amount") is not None:
        raise SystemExit(f"Expected method max_amount NULL, got {method.get('max_amount')!r}")

    subs = method.get("sub_options") or []
    if subs:
        raise SystemExit(f"Expected 0 sub-options, got {len(subs)}")

    tiers = method.get("tiers") or []
    if len(tiers) != 1:
        raise SystemExit(f"Expected 1 tier, got {len(tiers)}")

    tier = tiers[0]
    if tier.get("label") != DEFAULT_TIER_LABEL:
        raise SystemExit(f"Expected tier label {DEFAULT_TIER_LABEL!r}, got {tier.get('label')!r}")

    if Decimal(str(tier.get("min_amount"))) != Decimal("100"):
        raise SystemExit(f"Expected tier min_amount 100, got {tier.get('min_amount')!r}")
    if tier.get("max_amount") is not None:
        raise SystemExit(f"Expected tier max_amount NULL, got {tier.get('max_amount')!r}")

    if (tier.get("response_text") or "").strip():
        raise SystemExit("Expected empty tier response_text; copy lives on variants")

    if tier.get("use_group_checkout_link"):
        raise SystemExit("Expected use_group_checkout_link=false on Default tier")

    variants = tier.get("variants") or []
    if len(variants) != 4:
        raise SystemExit(f"Expected 4 variants on tier, got {len(variants)}")

    by_label = {v.get("label"): v for v in variants}
    if set(by_label.keys()) != set(EXPECTED_VARIANTS.keys()):
        raise SystemExit(f"Unexpected variant labels: {list(by_label.keys())}")

    for label, (weight, sort_order, url) in EXPECTED_VARIANTS.items():
        variant = by_label[label]
        if variant.get("weight") != weight:
            raise SystemExit(f"Expected {label!r} weight {weight}, got {variant.get('weight')!r}")
        if variant.get("sort_order") != sort_order:
            raise SystemExit(
                f"Expected {label!r} sort_order {sort_order}, got {variant.get('sort_order')!r}"
            )
        text = variant.get("response_text") or ""
        if not text.strip():
            raise SystemExit(f"Expected non-empty response_text on {label!r}")
        if url not in text:
            raise SystemExit(f"Expected {url!r} in {label!r} response_text")

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
        club, method, tier, variants = seed(session)
        if args.apply:
            session.commit()
            variant_ids = ", ".join(str(v.id) for v in variants)
            print(
                f"Seeded Round Table Venmo (v2): club_id={club.id}, method_id={method.id}, "
                f"tier_id={tier.id}, variant_ids=[{variant_ids}]"
            )
            verify_via_api(club.id)
            print(
                "API verification passed: 1 Default tier, 4 variants "
                "(weights 35/15/15/35), 0 sub-options."
            )
        else:
            session.rollback()
            print("Dry run (no changes committed). Would upsert:")
            print(f"  club: {club.name!r} (id={club.id})")
            print(
                f"  method: Venmo / {METHOD_SLUG} (deposit, min=$100, "
                f"accumulated=${ACCUMULATED_AMOUNT:,.2f}, sort_order=5)"
            )
            print(f"  tier: {DEFAULT_TIER_LABEL!r} ($100+, amount band only)")
            print("  variants:")
            for label, (weight, sort_order, url) in EXPECTED_VARIANTS.items():
                print(f"    - {label!r} weight={weight} sort_order={sort_order} ({url})")
            print("  sub-options: 0")
            print("Re-run with --apply to commit and verify.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
