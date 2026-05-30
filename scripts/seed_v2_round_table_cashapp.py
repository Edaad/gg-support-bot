#!/usr/bin/env python3
"""Seed Round Table deposit Cashapp into greenfield club_payment_* tables.

Idempotent upserts by (club_id, direction, slug), (method_id, tier label),
and (tier_id, variant label). Two tiers, three variants — all player copy on variants.

Usage (dry run — no writes):
    python scripts/seed_v2_round_table_cashapp.py

Apply + verify via /api/v2:
    python scripts/seed_v2_round_table_cashapp.py --apply
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path
from typing import Optional

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
METHOD_SLUG = "cashapp"
DIRECTION = "deposit"
ACCUMULATED_AMOUNT = Decimal("25456.00")

TIER_UNDER_LABEL = "Under $100"
TIER_OVER_LABEL = "Over $100"

VARIANT_UNDER_STRIPE_LABEL = "Stripe Cashapp (below $100)"
VARIANT_OVER_STRIPE_LABEL = "Cashapp Stripe"
VARIANT_OVER_ACCOUNT_LABEL = "Cashapp Account 1"

UNDER_CHECKOUT_RESPONSE_TEXT = """🚨 NO CREDIT CARDS. They will be refunded immediately

• Enter your deposit amount on the checkout page ($20 minimum, $100 maximum).

• Once sent, please inform us, and an agent will confirm the transaction and add your chips within 2 minutes!

• Just post a screenshot of your transaction, and it will be credited to your account!

{{hyperlink}}"""

OVER_STRIPE_RESPONSE_TEXT = """🚨 NO CREDIT CARDS. They will be refunded immediately

• Enter your deposit amount on the checkout page ($2000 maximum).

• Once sent, please inform us, and an agent will confirm the transaction and add your chips within 2 minutes!

• Just post a screenshot of your transaction, and it will be credited to your account!

{{hyperlink}}"""

OVER_ACCOUNT_RESPONSE_TEXT = """Cashapp: https://cash.app/$eduardok4444

• Please put a random emoji in the payment caption when sending

• Once sent, please send a screenshot, and an agent will confirm the transaction and add your chips within 2 minutes!"""


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

    method.name = "Cashapp"
    method.min_amount = Decimal("20")
    method.max_amount = None
    method.has_sub_options = False
    method.is_active = True
    method.sort_order = 4
    method.deposit_limit = None
    method.accumulated_amount = ACCUMULATED_AMOUNT
    session.flush()
    return method


def upsert_tier(
    session: Session,
    method_id: int,
    *,
    label: str,
    min_amount: Decimal,
    max_amount: Decimal,
    sort_order: int,
    checkout_min: Decimal,
    checkout_max: Decimal,
) -> ClubPaymentTier:
    tier = (
        session.query(ClubPaymentTier)
        .filter_by(method_id=method_id, label=label)
        .first()
    )
    if tier is None:
        tier = ClubPaymentTier(method_id=method_id, label=label)
        session.add(tier)

    tier.min_amount = min_amount
    tier.max_amount = max_amount
    tier.sort_order = sort_order
    tier.response_type = "text"
    clear_tier_response(tier)
    tier.use_group_checkout_link = True
    tier.group_checkout_provider = "stripe"
    tier.hyperlink_text = "PAY HERE"
    tier.checkout_min_amount = checkout_min
    tier.checkout_max_amount = checkout_max
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
    use_group_checkout_link: Optional[bool] = None,
    group_checkout_provider: Optional[str] = None,
    hyperlink_text: Optional[str] = None,
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
    variant.use_group_checkout_link = use_group_checkout_link
    variant.group_checkout_provider = group_checkout_provider
    variant.hyperlink_text = hyperlink_text
    variant.checkout_min_amount = None
    variant.checkout_max_amount = None
    session.flush()
    return variant


def seed(
    session: Session,
) -> tuple[Club, ClubPaymentMethod, list[ClubPaymentTier], list[ClubPaymentTierVariant]]:
    club = find_round_table_club(session)
    method = upsert_method(session, club.id)

    tier_under = upsert_tier(
        session,
        method.id,
        label=TIER_UNDER_LABEL,
        min_amount=Decimal("20"),
        max_amount=Decimal("100"),
        sort_order=0,
        checkout_min=Decimal("20"),
        checkout_max=Decimal("100"),
    )
    tier_over = upsert_tier(
        session,
        method.id,
        label=TIER_OVER_LABEL,
        min_amount=Decimal("101"),
        max_amount=Decimal("2000"),
        sort_order=1,
        checkout_min=Decimal("101"),
        checkout_max=Decimal("2000"),
    )

    variants = [
        upsert_variant(
            session,
            tier_under,
            label=VARIANT_UNDER_STRIPE_LABEL,
            weight=100,
            sort_order=0,
            response_text=UNDER_CHECKOUT_RESPONSE_TEXT,
            use_group_checkout_link=True,
            group_checkout_provider="stripe",
            hyperlink_text="PAY HERE",
        ),
        upsert_variant(
            session,
            tier_over,
            label=VARIANT_OVER_STRIPE_LABEL,
            weight=80,
            sort_order=0,
            response_text=OVER_STRIPE_RESPONSE_TEXT,
            use_group_checkout_link=None,
            group_checkout_provider=None,
            hyperlink_text=None,
        ),
        upsert_variant(
            session,
            tier_over,
            label=VARIANT_OVER_ACCOUNT_LABEL,
            weight=25,
            sort_order=1,
            response_text=OVER_ACCOUNT_RESPONSE_TEXT,
            use_group_checkout_link=False,
            group_checkout_provider=None,
            hyperlink_text=None,
        ),
    ]

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
    cashapp = [m for m in methods if m.get("slug") == METHOD_SLUG]
    if len(cashapp) != 1:
        raise SystemExit(f"Expected exactly one cashapp method, got {len(cashapp)}")

    method = cashapp[0]
    if method.get("has_sub_options"):
        raise SystemExit("Expected has_sub_options=false on Cashapp method")

    if Decimal(str(method.get("min_amount"))) != Decimal("20"):
        raise SystemExit(f"Expected method min_amount 20, got {method.get('min_amount')!r}")
    if method.get("max_amount") is not None:
        raise SystemExit(f"Expected method max_amount NULL, got {method.get('max_amount')!r}")

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
        if not tier.get("use_group_checkout_link"):
            raise SystemExit(f"Expected use_group_checkout_link=true on {tier.get('label')!r}")

    if Decimal(str(tier_under.get("min_amount"))) != Decimal("20"):
        raise SystemExit(f"Expected Under tier min 20, got {tier_under.get('min_amount')!r}")
    if Decimal(str(tier_under.get("max_amount"))) != Decimal("100"):
        raise SystemExit(f"Expected Under tier max 100, got {tier_under.get('max_amount')!r}")
    if Decimal(str(tier_over.get("min_amount"))) != Decimal("101"):
        raise SystemExit(f"Expected Over tier min 101, got {tier_over.get('min_amount')!r}")
    if Decimal(str(tier_over.get("max_amount"))) != Decimal("2000"):
        raise SystemExit(f"Expected Over tier max 2000, got {tier_over.get('max_amount')!r}")

    under_variants = tier_under.get("variants") or []
    if len(under_variants) != 1:
        raise SystemExit(f"Expected 1 variant on Under tier, got {len(under_variants)}")
    if under_variants[0].get("label") != VARIANT_UNDER_STRIPE_LABEL:
        raise SystemExit(f"Expected variant {VARIANT_UNDER_STRIPE_LABEL!r}")
    if under_variants[0].get("weight") != 100:
        raise SystemExit(f"Expected Under variant weight 100, got {under_variants[0].get('weight')!r}")
    if "{{hyperlink}}" not in (under_variants[0].get("response_text") or ""):
        raise SystemExit("Expected {{hyperlink}} in Under tier variant")

    over_variants = tier_over.get("variants") or []
    if len(over_variants) != 2:
        raise SystemExit(f"Expected 2 variants on Over tier, got {len(over_variants)}")
    by_label = {v.get("label"): v for v in over_variants}
    if set(by_label.keys()) != {VARIANT_OVER_STRIPE_LABEL, VARIANT_OVER_ACCOUNT_LABEL}:
        raise SystemExit(f"Unexpected Over tier variant labels: {list(by_label.keys())}")

    stripe_v = by_label[VARIANT_OVER_STRIPE_LABEL]
    account_v = by_label[VARIANT_OVER_ACCOUNT_LABEL]
    if stripe_v.get("weight") != 80:
        raise SystemExit(f"Expected Cashapp Stripe weight 80, got {stripe_v.get('weight')!r}")
    if account_v.get("weight") != 25:
        raise SystemExit(f"Expected Cashapp Account 1 weight 25, got {account_v.get('weight')!r}")
    if "{{hyperlink}}" not in (stripe_v.get("response_text") or ""):
        raise SystemExit("Expected {{hyperlink}} in Cashapp Stripe variant")
    if account_v.get("use_group_checkout_link") is not False:
        raise SystemExit(
            f"Expected use_group_checkout_link=false on Account 1, got {account_v.get('use_group_checkout_link')!r}"
        )

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
        club, method, tiers, variants = seed(session)
        if args.apply:
            session.commit()
            tier_ids = ", ".join(str(t.id) for t in tiers)
            variant_ids = ", ".join(str(v.id) for v in variants)
            print(
                f"Seeded Round Table Cashapp (v2): club_id={club.id}, method_id={method.id}, "
                f"tier_ids=[{tier_ids}], variant_ids=[{variant_ids}]"
            )
            verify_via_api(club.id)
            print(
                "API verification passed: 2 tiers (Under $100, Over $100), 3 variants "
                "(weights 100/80/25), Stripe on tiers, 0 sub-options."
            )
        else:
            session.rollback()
            print("Dry run (no changes committed). Would upsert:")
            print(f"  club: {club.name!r} (id={club.id})")
            print(
                f"  method: Cashapp / {METHOD_SLUG} (deposit, min=$20, "
                f"accumulated=${ACCUMULATED_AMOUNT:,.2f}, sort_order=4)"
            )
            print(f"  tier: {TIER_UNDER_LABEL!r} ($20–$100, Stripe defaults, 1 variant)")
            print(f"  tier: {TIER_OVER_LABEL!r} ($101–$2000, Stripe defaults, 2 variants)")
            print("  sub-options: 0")
            print("Re-run with --apply to commit and verify.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
