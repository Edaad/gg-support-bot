#!/usr/bin/env python3
"""Seed ClubGTO deposit Venmo into greenfield club_payment_* tables.

Idempotent upserts by (club_id, direction, slug), (method_id, tier label),
and (tier_id, variant label). Two tiers, five photo variants — all player copy
on variants. Preserves legacy caption vs response_text field layout.

Usage (dry run — no writes):
    python scripts/seed_v2_clubgto_venmo.py

Apply + verify via /api/v2:
    python scripts/seed_v2_clubgto_venmo.py --apply
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

CLUB_NAME = "ClubGTO"
METHOD_SLUG = "venmo"
DIRECTION = "deposit"

TIER_UNDER_LABEL = "Under $100"
TIER_OVER_LABEL = "Over $100"

VARIANT_UNDER_DEFAULT_LABEL = "Default"
VARIANT_CLUB_ROUND_LABEL = "Venmo (@club-round)"
VARIANT_GODFATHER_LABEL = "Venmo (@godfather4444)"
VARIANT_JAGGER_LABEL = "Venmo (@jagger4444)"
VARIANT_MICHAEL_LABEL = "Venmo (@michaelc444)"

VENMO_PHOTO_FILE_IDS = (
    "AgACAgEAAxkBAAEBDbZpvbuSXAxNX3KDV3FlKbCXGzsCRQACZAtrGymu8UUnr3pk6mOongEAAwIAA3kAAzoE,"
    "AgACAgEAAxkBAAEBDbdpvbuSh-dpzIdmveUsNf0ydCc00QACZQtrGymu8UUnfjEDylFQogEAAwIAA3kAAzoE"
)

UNDER_DEFAULT_CAPTION = """Venmo: https://venmo.com/u/janseashells

Please use food emoji as description and send us a screenshot with full transaction details when complete.  Thanks!

• Please send as a personal payment
• Credit Card Payments will not be accepted"""

OVER_CLUB_ROUND_CAPTION = """Venmo: https://venmo.com/u/club-round

• Please put a random emoji in the payment caption when sending

• Once sent, please send screenshots such as the ones posted above, and an agent will confirm the transaction and add your chips within 2 minutes!"""

OVER_GODFATHER_CAPTION = """Venmo: https://venmo.com/u/godfather4444

• Please put a random emoji in the payment caption when sending

• Once sent, please send screenshots such as the ones posted above, and an agent will confirm the transaction and add your chips within 2 minutes!"""

OVER_JAGGER_RESPONSE_TEXT = """Venmo: https://venmo.com/u/jagger4444

• Please put a random emoji in the payment caption when you sending

• Once sent, please send a screenshot, and an agent will confirm the transaction and add your chips within 2 minutes!"""

OVER_MICHAEL_CAPTION = """Venmo: https://venmo.com/u/michaelc4444

• Please put a random emoji in the payment caption when you sending

• Once sent, please send a screenshot, and an agent will confirm the transaction and add your chips within 2 minutes!"""

EXPECTED_OVER_VARIANTS: dict[str, tuple[int, int]] = {
    VARIANT_CLUB_ROUND_LABEL: (35, 0),
    VARIANT_GODFATHER_LABEL: (35, 1),
    VARIANT_JAGGER_LABEL: (15, 2),
    VARIANT_MICHAEL_LABEL: (15, 3),
}


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

    method.name = "Venmo"
    method.min_amount = Decimal("50")
    method.max_amount = None
    method.has_sub_options = False
    method.is_active = True
    method.sort_order = 5
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

    tier.min_amount = Decimal("50")
    tier.max_amount = Decimal("99")
    tier.sort_order = 0
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

    tier.min_amount = Decimal("100")
    tier.max_amount = None
    tier.sort_order = 1
    clear_tier_response(tier)
    tier.use_group_checkout_link = False
    tier.group_checkout_provider = None
    tier.hyperlink_text = None
    tier.checkout_min_amount = None
    tier.checkout_max_amount = None
    session.flush()
    return tier


def upsert_photo_variant(
    session: Session,
    tier: ClubPaymentTier,
    *,
    label: str,
    weight: int,
    sort_order: int,
    response_text: Optional[str] = None,
    response_caption: Optional[str] = None,
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
    variant.response_type = "photo"
    variant.response_text = response_text
    variant.response_file_id = VENMO_PHOTO_FILE_IDS
    variant.response_caption = response_caption
    variant.use_group_checkout_link = False
    variant.group_checkout_provider = None
    variant.hyperlink_text = None
    variant.checkout_min_amount = None
    variant.checkout_max_amount = None
    session.flush()
    return variant


def seed(
    session: Session,
) -> tuple[Club, ClubPaymentMethod, list[ClubPaymentTier], list[ClubPaymentTierVariant]]:
    club = find_clubgto_club(session)
    method = upsert_method(session, club.id)
    tier_under = upsert_under_tier(session, method.id)
    tier_over = upsert_over_tier(session, method.id)

    variants = [
        upsert_photo_variant(
            session,
            tier_under,
            label=VARIANT_UNDER_DEFAULT_LABEL,
            weight=100,
            sort_order=0,
            response_caption=UNDER_DEFAULT_CAPTION,
        ),
        upsert_photo_variant(
            session,
            tier_over,
            label=VARIANT_CLUB_ROUND_LABEL,
            weight=35,
            sort_order=0,
            response_caption=OVER_CLUB_ROUND_CAPTION,
        ),
        upsert_photo_variant(
            session,
            tier_over,
            label=VARIANT_GODFATHER_LABEL,
            weight=35,
            sort_order=1,
            response_caption=OVER_GODFATHER_CAPTION,
        ),
        upsert_photo_variant(
            session,
            tier_over,
            label=VARIANT_JAGGER_LABEL,
            weight=15,
            sort_order=2,
            response_text=OVER_JAGGER_RESPONSE_TEXT,
        ),
        upsert_photo_variant(
            session,
            tier_over,
            label=VARIANT_MICHAEL_LABEL,
            weight=15,
            sort_order=3,
            response_caption=OVER_MICHAEL_CAPTION,
        ),
    ]

    return club, method, [tier_under, tier_over], variants


def _tier_by_label(tiers: list[dict], label: str) -> dict:
    matches = [t for t in tiers if t.get("label") == label]
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one tier {label!r}, got {len(matches)}")
    return matches[0]


def _assert_photo_variant(variant: dict, *, label: str) -> None:
    if variant.get("label") != label:
        raise SystemExit(f"Expected variant label {label!r}, got {variant.get('label')!r}")
    if variant.get("response_type") != "photo":
        raise SystemExit(f"Expected response_type=photo on {label!r}")
    file_id = variant.get("response_file_id") or ""
    if "AgACAgEAAxkBAAEBDbZpvbuSXAxNX3KDV3FlKbCXGzsCRQ" not in file_id:
        raise SystemExit(f"Expected shared photo file IDs on {label!r}")


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

    if Decimal(str(method.get("min_amount"))) != Decimal("50"):
        raise SystemExit(f"Expected method min_amount 50, got {method.get('min_amount')!r}")
    if method.get("max_amount") is not None:
        raise SystemExit(f"Expected method max_amount NULL, got {method.get('max_amount')!r}")
    if method.get("sort_order") != 5:
        raise SystemExit(f"Expected sort_order 5, got {method.get('sort_order')!r}")

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
            raise SystemExit(f"Expected no Stripe on {tier.get('label')!r}")

    if Decimal(str(tier_under.get("min_amount"))) != Decimal("50"):
        raise SystemExit(f"Expected Under tier min 50, got {tier_under.get('min_amount')!r}")
    if Decimal(str(tier_under.get("max_amount"))) != Decimal("99"):
        raise SystemExit(f"Expected Under tier max 99, got {tier_under.get('max_amount')!r}")
    if Decimal(str(tier_over.get("min_amount"))) != Decimal("100"):
        raise SystemExit(f"Expected Over tier min 100, got {tier_over.get('min_amount')!r}")
    if tier_over.get("max_amount") is not None:
        raise SystemExit(f"Expected Over tier max_amount NULL, got {tier_over.get('max_amount')!r}")

    under_variants = tier_under.get("variants") or []
    if len(under_variants) != 1:
        raise SystemExit(f"Expected 1 variant on Under tier, got {len(under_variants)}")
    under_default = under_variants[0]
    _assert_photo_variant(under_default, label=VARIANT_UNDER_DEFAULT_LABEL)
    if under_default.get("weight") != 100:
        raise SystemExit(f"Expected Under Default weight 100, got {under_default.get('weight')!r}")
    caption = under_default.get("response_caption") or ""
    if "janseashells" not in caption:
        raise SystemExit("Expected janseashells in Under Default response_caption")
    if (under_default.get("response_text") or "").strip():
        raise SystemExit("Expected empty response_text on Under Default variant")

    over_variants = tier_over.get("variants") or []
    if len(over_variants) != 4:
        raise SystemExit(f"Expected 4 variants on Over tier, got {len(over_variants)}")
    by_label = {v.get("label"): v for v in over_variants}
    if set(by_label.keys()) != set(EXPECTED_OVER_VARIANTS.keys()):
        raise SystemExit(f"Unexpected Over tier variant labels: {list(by_label.keys())}")

    for label, (weight, _sort) in EXPECTED_OVER_VARIANTS.items():
        variant = by_label[label]
        _assert_photo_variant(variant, label=label)
        if variant.get("weight") != weight:
            raise SystemExit(f"Expected {label!r} weight {weight}, got {variant.get('weight')!r}")

    jagger = by_label[VARIANT_JAGGER_LABEL]
    if not (jagger.get("response_text") or "").strip():
        raise SystemExit("Expected non-empty response_text on Venmo (@jagger4444)")
    if "jagger4444" not in (jagger.get("response_text") or ""):
        raise SystemExit("Expected jagger4444 in jagger variant response_text")
    if (jagger.get("response_caption") or "").strip():
        raise SystemExit("Expected empty response_caption on Venmo (@jagger4444)")

    for label in (VARIANT_CLUB_ROUND_LABEL, VARIANT_GODFATHER_LABEL, VARIANT_MICHAEL_LABEL):
        variant = by_label[label]
        cap = variant.get("response_caption") or ""
        if not cap.strip():
            raise SystemExit(f"Expected non-empty response_caption on {label!r}")
        if (variant.get("response_text") or "").strip():
            raise SystemExit(f"Expected empty response_text on {label!r}")


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
                f"Seeded ClubGTO Venmo (v2): club_id={club.id}, method_id={method.id}, "
                f"tier_ids=[{tier_ids}], variant_ids=[{variant_ids}]"
            )
            verify_via_api(club.id)
            print(
                "API verification passed: 2 tiers (Under $100, Over $100), 5 photo variants "
                "(weights 100/35/35/15/15), 0 sub-options."
            )
        else:
            session.rollback()
            print("Dry run (no changes committed). Would upsert:")
            print(f"  club: {club.name!r} (id={club.id})")
            print(
                f"  method: Venmo / {METHOD_SLUG} (deposit, min=$50, "
                f"accumulated=null, sort_order=5)"
            )
            print(f"  tier: {TIER_UNDER_LABEL!r} ($50–$99, 1 Default photo variant)")
            print(f"  tier: {TIER_OVER_LABEL!r} ($100+, 4 photo variants 35/35/15/15)")
            print("  sub-options: 0")
            print("Re-run with --apply to commit and verify.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
