#!/usr/bin/env python3
"""Seed Creator Club deposit Cashapp into greenfield club_payment_* tables.

Idempotent upserts by (club_id, direction, slug), (method_id, tier label),
and (tier_id, variant label). Two tiers, three variants — all player copy on
variants. Does not seed the legacy hardcoded buy.stripe.com variant.

Usage (dry run — no writes):
    python scripts/seed_v2_creator_club_cashapp.py

Apply + verify via /api/v2:
    python scripts/seed_v2_creator_club_cashapp.py --apply
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

CLUB_NAME = "Creator Club"
METHOD_SLUG = "cashapp"
DIRECTION = "deposit"
ACCUMULATED_AMOUNT = Decimal("14146.00")

TIER_UNDER_LABEL = "Under $100"
TIER_OVER_LABEL = "Over $100"

VARIANT_UNDER_DEFAULT_LABEL = "Default"
VARIANT_OVER_STRIPE_LABEL = "Cashapp Stripe"
VARIANT_OVER_EDUARDO_LABEL = "Cashapp Eduardo"

EXCLUDED_BUY_LINK = "buy.stripe.com/eVq28j62b8mAbkn7nobMQ0a"

UNDER_DEFAULT_RESPONSE_TEXT = """$100 MAX

Cashapp: {{hyperlink}}
• Once sent, please inform us, and an agent will confirm the transaction and add your chips within 2 minutes.
• Just post a screenshot of your transaction, and it will be credited to your account!"""

OVER_STRIPE_RESPONSE_TEXT = """🚨 NO CREDIT CARDS. They will be refunded immediately

• Enter your deposit amount on the checkout page ($2000 maximum).

• Once sent, please inform us, and an agent will confirm the transaction and add your chips within 2 minutes!

• Just post a screenshot of your transaction, and it will be credited to your account!

{{hyperlink}}"""

OVER_EDUARDO_RESPONSE_TEXT = """Cashapp: https://cash.app/$eduardok4444

• Please put a random emoji in the payment caption when sending

• Once sent, please send a screenshot, and an agent will confirm the transaction and add your chips within 2 minutes!"""


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

    method.name = "Cashapp"
    method.min_amount = Decimal("20")
    method.max_amount = None
    method.has_sub_options = False
    method.is_active = True
    method.sort_order = 5
    method.deposit_limit = None
    method.accumulated_amount = ACCUMULATED_AMOUNT
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
    tier.max_amount = Decimal("100")
    tier.sort_order = 0
    tier.response_type = "text"
    clear_tier_response(tier)
    tier.use_group_checkout_link = True
    tier.group_checkout_provider = "stripe"
    tier.hyperlink_text = "PAY HERE"
    tier.checkout_min_amount = Decimal("20")
    tier.checkout_max_amount = Decimal("100")
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

    tier.min_amount = Decimal("101")
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
    club = find_creator_club(session)
    method = upsert_method(session, club.id)
    tier_under = upsert_under_tier(session, method.id)
    tier_over = upsert_over_tier(session, method.id)

    variants = [
        upsert_variant(
            session,
            tier_under,
            label=VARIANT_UNDER_DEFAULT_LABEL,
            weight=100,
            sort_order=0,
            response_text=UNDER_DEFAULT_RESPONSE_TEXT,
            use_group_checkout_link=None,
            group_checkout_provider=None,
            hyperlink_text=None,
        ),
        upsert_variant(
            session,
            tier_over,
            label=VARIANT_OVER_STRIPE_LABEL,
            weight=80,
            sort_order=0,
            response_text=OVER_STRIPE_RESPONSE_TEXT,
            use_group_checkout_link=True,
            group_checkout_provider="stripe",
            hyperlink_text="PAY HERE",
        ),
        upsert_variant(
            session,
            tier_over,
            label=VARIANT_OVER_EDUARDO_LABEL,
            weight=20,
            sort_order=1,
            response_text=OVER_EDUARDO_RESPONSE_TEXT,
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


def _assert_no_buy_link(method: dict) -> None:
    for tier in method.get("tiers") or []:
        for variant in tier.get("variants") or []:
            text = variant.get("response_text") or ""
            if EXCLUDED_BUY_LINK in text:
                raise SystemExit(
                    f"Found excluded buy-link in variant {variant.get('label')!r}"
                )


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
    if method.get("sort_order") != 5:
        raise SystemExit(f"Expected sort_order 5, got {method.get('sort_order')!r}")

    subs = method.get("sub_options") or []
    if subs:
        raise SystemExit(f"Expected 0 sub-options, got {len(subs)}")

    tiers = method.get("tiers") or []
    if len(tiers) != 2:
        raise SystemExit(f"Expected 2 tiers, got {len(tiers)}")

    tier_under = _tier_by_label(tiers, TIER_UNDER_LABEL)
    tier_over = _tier_by_label(tiers, TIER_OVER_LABEL)

    if (tier_under.get("response_text") or "").strip():
        raise SystemExit("Expected empty tier response_text on Under $100")
    if not tier_under.get("use_group_checkout_link"):
        raise SystemExit("Expected use_group_checkout_link=true on Under $100 tier")

    if (tier_over.get("response_text") or "").strip():
        raise SystemExit("Expected empty tier response_text on Over $100")
    if tier_over.get("use_group_checkout_link"):
        raise SystemExit("Expected use_group_checkout_link=false on Over $100 tier")

    if Decimal(str(tier_under.get("min_amount"))) != Decimal("20"):
        raise SystemExit(f"Expected Under tier min 20, got {tier_under.get('min_amount')!r}")
    if Decimal(str(tier_under.get("max_amount"))) != Decimal("100"):
        raise SystemExit(f"Expected Under tier max 100, got {tier_under.get('max_amount')!r}")
    if Decimal(str(tier_over.get("min_amount"))) != Decimal("101"):
        raise SystemExit(f"Expected Over tier min 101, got {tier_over.get('min_amount')!r}")
    if tier_over.get("max_amount") is not None:
        raise SystemExit(f"Expected Over tier max_amount NULL, got {tier_over.get('max_amount')!r}")

    under_variants = tier_under.get("variants") or []
    if len(under_variants) != 1:
        raise SystemExit(f"Expected 1 variant on Under tier, got {len(under_variants)}")
    if under_variants[0].get("label") != VARIANT_UNDER_DEFAULT_LABEL:
        raise SystemExit(f"Expected variant {VARIANT_UNDER_DEFAULT_LABEL!r}")
    if under_variants[0].get("weight") != 100:
        raise SystemExit(f"Expected Under variant weight 100, got {under_variants[0].get('weight')!r}")
    if "{{hyperlink}}" not in (under_variants[0].get("response_text") or ""):
        raise SystemExit("Expected {{hyperlink}} in Under tier Default variant")

    over_variants = tier_over.get("variants") or []
    if len(over_variants) != 2:
        raise SystemExit(f"Expected 2 variants on Over tier, got {len(over_variants)}")
    by_label = {v.get("label"): v for v in over_variants}
    if set(by_label.keys()) != {VARIANT_OVER_STRIPE_LABEL, VARIANT_OVER_EDUARDO_LABEL}:
        raise SystemExit(f"Unexpected Over tier variant labels: {list(by_label.keys())}")

    stripe_v = by_label[VARIANT_OVER_STRIPE_LABEL]
    eduardo_v = by_label[VARIANT_OVER_EDUARDO_LABEL]
    if stripe_v.get("weight") != 80:
        raise SystemExit(f"Expected Cashapp Stripe weight 80, got {stripe_v.get('weight')!r}")
    if eduardo_v.get("weight") != 20:
        raise SystemExit(f"Expected Cashapp Eduardo weight 20, got {eduardo_v.get('weight')!r}")
    if not stripe_v.get("use_group_checkout_link"):
        raise SystemExit("Expected use_group_checkout_link=true on Cashapp Stripe variant")
    if "{{hyperlink}}" not in (stripe_v.get("response_text") or ""):
        raise SystemExit("Expected {{hyperlink}} in Cashapp Stripe variant")
    if eduardo_v.get("use_group_checkout_link") is not False:
        raise SystemExit(
            f"Expected use_group_checkout_link=false on Cashapp Eduardo, "
            f"got {eduardo_v.get('use_group_checkout_link')!r}"
        )
    if "cash.app/$eduardok4444" not in (eduardo_v.get("response_text") or ""):
        raise SystemExit("Expected cash.app/$eduardok4444 in Cashapp Eduardo variant")

    total_variants = len(under_variants) + len(over_variants)
    if total_variants != 3:
        raise SystemExit(f"Expected 3 variants total, got {total_variants}")

    _assert_no_buy_link(method)

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
                f"Seeded Creator Club Cashapp (v2): club_id={club.id}, method_id={method.id}, "
                f"tier_ids=[{tier_ids}], variant_ids=[{variant_ids}]"
            )
            verify_via_api(club.id)
            print(
                "API verification passed: 2 tiers (Under $100, Over $100), 3 variants "
                "(weights 100/80/20), no buy-link variant, 0 sub-options."
            )
        else:
            session.rollback()
            print("Dry run (no changes committed). Would upsert:")
            print(f"  club: {club.name!r} (id={club.id})")
            print(
                f"  method: Cashapp / {METHOD_SLUG} (deposit, min=$20, "
                f"accumulated=${ACCUMULATED_AMOUNT:,.2f}, sort_order=5)"
            )
            print(f"  tier: {TIER_UNDER_LABEL!r} ($20–$100, Stripe on tier, 1 Default variant)")
            print(f"  tier: {TIER_OVER_LABEL!r} ($101+, no Stripe on tier, 2 variants 80/20)")
            print("  excluded: hardcoded buy.stripe.com variant")
            print("  sub-options: 0")
            print("Re-run with --apply to commit and verify.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
