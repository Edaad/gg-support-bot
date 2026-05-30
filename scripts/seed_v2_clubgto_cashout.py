#!/usr/bin/env python3
"""Seed ClubGTO cashout methods into greenfield club_payment_* tables.

Idempotent upserts for Crypto (12 sub-options), Cashapp (min $13), Zelle, and Venmo.
Player copy on sub-options (crypto) or Default variants (text methods).

Usage (dry run — no writes):
    python scripts/seed_v2_clubgto_cashout.py

Apply + verify via /api/v2:
    python scripts/seed_v2_clubgto_cashout.py --apply
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
from db.models import (
    Club,
    ClubPaymentMethod,
    ClubPaymentSubOption,
    ClubPaymentTier,
    ClubPaymentTierVariant,
)

CLUB_NAME = "ClubGTO"
DIRECTION = "cashout"
DEFAULT_TIER_LABEL = "Default"
DEFAULT_MIN_AMOUNT = Decimal("50")
CASHAPP_MIN_AMOUNT = Decimal("13")
ZERO_ACCUMULATED = Decimal("0")

CRYPTO_SUB_OPTIONS: list[dict[str, str]] = [
    {"name": "Bitcoin", "slug": "btc"},
    {"name": "Ethereum", "slug": "eth"},
    {"name": "Litecoin", "slug": "ltc"},
    {"name": "Solana", "slug": "sol"},
    {"name": "Tron", "slug": "trx"},
    {"name": "USDC ERC20", "slug": "usdcerc20"},
    {"name": "USDC SOL", "slug": "usdcsol"},
    {"name": "USDT ERC20", "slug": "usdterc20"},
    {"name": "USDT SOL", "slug": "usdtsol"},
    {"name": "USDT TRC20", "slug": "usdttrc20"},
    {"name": "XRP", "slug": "xrp"},
    {"name": "USDT BEP20", "slug": "usdtbep20"},
]

CRYPTO_SUB_RESPONSE_TEXT = """Please provide your Address for the selected Crypto Asset

• We will have your cashout sent instantly (between 8am-11pm EST)!

Thank you!"""

CASHAPP_RESPONSE_TEXT = """• Please provide the LINK for your Cashapp (no $ tags please).

• We will have your cashout sent instantly (between 8am-11pm EST)!

Thank you!"""

ZELLE_RESPONSE_TEXT = """• Please provide the Phone Number / Email of your Zelle

• We will have your cashout sent instantly (between 8am-11pm EST)!

Thank you!"""

VENMO_RESPONSE_TEXT = """• Please provide the LINK for your Venmo (no @ tags please).

• We will have your cashout sent instantly (between 8am-11pm EST)!

Thank you!"""

TEXT_METHODS: list[dict] = [
    {
        "slug": "cashapp",
        "name": "Cashapp",
        "sort_order": 0,
        "min_amount": CASHAPP_MIN_AMOUNT,
        "response_text": CASHAPP_RESPONSE_TEXT,
    },
    {
        "slug": "zelle",
        "name": "Zelle",
        "sort_order": 1,
        "min_amount": DEFAULT_MIN_AMOUNT,
        "response_text": ZELLE_RESPONSE_TEXT,
    },
    {
        "slug": "venmo",
        "name": "Venmo",
        "sort_order": 2,
        "min_amount": DEFAULT_MIN_AMOUNT,
        "response_text": VENMO_RESPONSE_TEXT,
    },
]

EXPECTED_CRYPTO_SLUGS = {row["slug"] for row in CRYPTO_SUB_OPTIONS}
EXPECTED_TEXT_SLUGS = {row["slug"] for row in TEXT_METHODS}
TEXT_MIN_BY_SLUG = {row["slug"]: row["min_amount"] for row in TEXT_METHODS}


def find_clubgto_club(session: Session) -> Club:
    matches = session.query(Club).filter(Club.name == CLUB_NAME).all()
    if not matches:
        raise SystemExit(f"Club not found: {CLUB_NAME!r}")
    if len(matches) > 1:
        ids = [c.id for c in matches]
        raise SystemExit(f"Multiple clubs named {CLUB_NAME!r}: ids={ids}")
    return matches[0]


def _clear_tier_stripe_fields(tier: ClubPaymentTier) -> None:
    tier.response_type = "text"
    tier.response_text = None
    tier.response_file_id = None
    tier.response_caption = None
    tier.use_group_checkout_link = False
    tier.group_checkout_provider = None
    tier.hyperlink_text = None
    tier.checkout_min_amount = None
    tier.checkout_max_amount = None


def upsert_default_tier(
    session: Session, method_id: int, *, min_amount: Decimal
) -> ClubPaymentTier:
    tier = (
        session.query(ClubPaymentTier)
        .filter_by(method_id=method_id, label=DEFAULT_TIER_LABEL)
        .first()
    )
    if tier is None:
        tier = ClubPaymentTier(method_id=method_id, label=DEFAULT_TIER_LABEL)
        session.add(tier)

    tier.min_amount = min_amount
    tier.max_amount = None
    tier.sort_order = 0
    _clear_tier_stripe_fields(tier)
    session.flush()
    return tier


def upsert_crypto_method(
    session: Session, club_id: int
) -> tuple[ClubPaymentMethod, ClubPaymentTier, list[ClubPaymentSubOption]]:
    method = (
        session.query(ClubPaymentMethod)
        .filter_by(club_id=club_id, direction=DIRECTION, slug="crypto")
        .first()
    )
    if method is None:
        method = ClubPaymentMethod(club_id=club_id, direction=DIRECTION, slug="crypto")
        session.add(method)

    method.name = "Crypto"
    method.min_amount = DEFAULT_MIN_AMOUNT
    method.max_amount = None
    method.has_sub_options = True
    method.is_active = True
    method.sort_order = 0
    method.deposit_limit = None
    method.accumulated_amount = ZERO_ACCUMULATED
    session.flush()

    tier = upsert_default_tier(session, method.id, min_amount=DEFAULT_MIN_AMOUNT)

    subs: list[ClubPaymentSubOption] = []
    for spec in CRYPTO_SUB_OPTIONS:
        sub = (
            session.query(ClubPaymentSubOption)
            .filter_by(method_id=method.id, slug=spec["slug"])
            .first()
        )
        if sub is None:
            sub = ClubPaymentSubOption(method_id=method.id, slug=spec["slug"])
            session.add(sub)

        sub.name = spec["name"]
        sub.response_type = "text"
        sub.response_text = CRYPTO_SUB_RESPONSE_TEXT
        sub.response_file_id = None
        sub.response_caption = None
        sub.is_active = True
        sub.sort_order = 0
        subs.append(sub)

    session.flush()
    return method, tier, subs


def upsert_text_cashout_method(
    session: Session,
    club_id: int,
    *,
    slug: str,
    name: str,
    sort_order: int,
    min_amount: Decimal,
    response_text: str,
) -> tuple[ClubPaymentMethod, ClubPaymentTier, ClubPaymentTierVariant]:
    method = (
        session.query(ClubPaymentMethod)
        .filter_by(club_id=club_id, direction=DIRECTION, slug=slug)
        .first()
    )
    if method is None:
        method = ClubPaymentMethod(club_id=club_id, direction=DIRECTION, slug=slug)
        session.add(method)

    method.name = name
    method.min_amount = min_amount
    method.max_amount = None
    method.has_sub_options = False
    method.is_active = True
    method.sort_order = sort_order
    method.deposit_limit = None
    method.accumulated_amount = ZERO_ACCUMULATED
    session.flush()

    tier = upsert_default_tier(session, method.id, min_amount=min_amount)
    variant = upsert_default_variant(session, tier, response_text=response_text)
    return method, tier, variant


def seed(session: Session) -> dict:
    club = find_clubgto_club(session)

    crypto_method, crypto_tier, crypto_subs = upsert_crypto_method(session, club.id)

    text_results = {}
    for spec in TEXT_METHODS:
        method, tier, variant = upsert_text_cashout_method(
            session,
            club.id,
            slug=spec["slug"],
            name=spec["name"],
            sort_order=spec["sort_order"],
            min_amount=spec["min_amount"],
            response_text=spec["response_text"],
        )
        text_results[spec["slug"]] = (method, tier, variant)

    return {
        "club": club,
        "crypto": (crypto_method, crypto_tier, crypto_subs),
        "text": text_results,
    }


def _is_null_or_zero_accumulated(value) -> bool:
    if value is None:
        return True
    return Decimal(str(value)) == Decimal("0")


def _verify_crypto_method(method: dict) -> None:
    if method.get("slug") != "crypto":
        raise SystemExit(f"Expected crypto slug, got {method.get('slug')!r}")
    if not method.get("has_sub_options"):
        raise SystemExit("Expected has_sub_options=true on crypto cashout method")

    min_amount = method.get("min_amount")
    if min_amount is None or Decimal(str(min_amount)) != DEFAULT_MIN_AMOUNT:
        raise SystemExit(f"Expected crypto min_amount={DEFAULT_MIN_AMOUNT}, got {min_amount!r}")

    if not _is_null_or_zero_accumulated(method.get("accumulated_amount")):
        raise SystemExit("Expected accumulated_amount=null/0 on crypto cashout method")

    tiers = method.get("tiers") or []
    if len(tiers) != 1:
        raise SystemExit(f"Expected 1 crypto tier, got {len(tiers)}")
    if tiers[0].get("label") != DEFAULT_TIER_LABEL:
        raise SystemExit(f"Expected tier label {DEFAULT_TIER_LABEL!r} on crypto")

    if (tiers[0].get("response_text") or "").strip():
        raise SystemExit("Expected empty tier response_text on crypto; copy on sub-options")

    variants = tiers[0].get("variants") or []
    if variants:
        raise SystemExit(f"Expected 0 variants on crypto tier, got {len(variants)}")

    subs = method.get("sub_options") or []
    if len(subs) != 12:
        raise SystemExit(f"Expected 12 crypto sub-options, got {len(subs)}")

    slugs = {s.get("slug") for s in subs}
    if slugs != EXPECTED_CRYPTO_SLUGS:
        missing = EXPECTED_CRYPTO_SLUGS - slugs
        extra = slugs - EXPECTED_CRYPTO_SLUGS
        raise SystemExit(f"Crypto sub-option slug mismatch: missing={missing}, extra={extra}")


def _verify_text_method(method: dict, slug: str) -> None:
    expected_min = TEXT_MIN_BY_SLUG[slug]
    if method.get("slug") != slug:
        raise SystemExit(f"Expected slug {slug!r}, got {method.get('slug')!r}")
    if method.get("has_sub_options"):
        raise SystemExit(f"Expected has_sub_options=false on {slug} cashout method")

    min_amount = method.get("min_amount")
    if min_amount is None or Decimal(str(min_amount)) != expected_min:
        raise SystemExit(f"Expected {slug} min_amount={expected_min}, got {min_amount!r}")

    if not _is_null_or_zero_accumulated(method.get("accumulated_amount")):
        raise SystemExit(f"Expected accumulated_amount=null/0 on {slug} cashout method")

    subs = method.get("sub_options") or []
    if subs:
        raise SystemExit(f"Expected 0 sub-options on {slug}, got {len(subs)}")

    tiers = method.get("tiers") or []
    if len(tiers) != 1:
        raise SystemExit(f"Expected 1 tier on {slug}, got {len(tiers)}")
    if tiers[0].get("label") != DEFAULT_TIER_LABEL:
        raise SystemExit(f"Expected tier label {DEFAULT_TIER_LABEL!r} on {slug}")

    tier_min = tiers[0].get("min_amount")
    if tier_min is None or Decimal(str(tier_min)) != expected_min:
        raise SystemExit(f"Expected tier min_amount={expected_min} on {slug}, got {tier_min!r}")

    if (tiers[0].get("response_text") or "").strip():
        raise SystemExit(f"Expected empty tier response_text on {slug}; copy lives on variant")

    variants = tiers[0].get("variants") or []
    if len(variants) != 1:
        raise SystemExit(f"Expected 1 variant on {slug} tier, got {len(variants)}")
    if variants[0].get("label") != DEFAULT_TIER_LABEL:
        raise SystemExit(f"Expected variant label {DEFAULT_TIER_LABEL!r} on {slug}")
    if not (variants[0].get("response_text") or "").strip():
        raise SystemExit(f"Expected non-empty response_text on {slug} Default variant")


def verify_via_api(club_id: int) -> None:
    from fastapi.testclient import TestClient

    from api.app import create_app
    from api.auth import create_token

    client = TestClient(create_app())
    token = create_token()
    resp = client.get(
        f"/api/v2/clubs/{club_id}/methods?direction={DIRECTION}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 200:
        raise SystemExit(f"API verify failed: HTTP {resp.status_code} — {resp.text}")

    methods = resp.json()
    by_slug = {m.get("slug"): m for m in methods}

    expected_slugs = {"crypto"} | EXPECTED_TEXT_SLUGS
    missing = expected_slugs - set(by_slug)
    if missing:
        raise SystemExit(f"Missing cashout methods in API response: {missing}")

    _verify_crypto_method(by_slug["crypto"])
    for slug in sorted(EXPECTED_TEXT_SLUGS):
        _verify_text_method(by_slug[slug], slug)


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
        result = seed(session)
        club = result["club"]
        crypto_method, crypto_tier, crypto_subs = result["crypto"]

        if args.apply:
            session.commit()
            print(
                f"Seeded ClubGTO cashout (v2): club_id={club.id}, "
                f"crypto method_id={crypto_method.id}, sub_options={len(crypto_subs)}"
            )
            for slug, (method, _tier, _variant) in result["text"].items():
                min_amt = TEXT_MIN_BY_SLUG[slug]
                print(f"  {slug}: method_id={method.id}, min=${min_amt}")
            verify_via_api(club.id)
            print(
                "API verification passed: 4 methods (crypto + cashapp + zelle + venmo), "
                "direction=cashout."
            )
        else:
            session.rollback()
            print("Dry run (no changes committed). Would upsert:")
            print(f"  club: {club.name!r} (id={club.id})")
            print(
                f"  crypto: min=${DEFAULT_MIN_AMOUNT}, sort_order=0, "
                f"1 Default tier, {len(crypto_subs)} sub-options"
            )
            for spec in TEXT_METHODS:
                method, tier, variant = result["text"][spec["slug"]]
                print(
                    f"  {spec['name']} / {spec['slug']}: min=${spec['min_amount']}, "
                    f"sort_order={spec['sort_order']}, tier_id={tier.id}, variant_id={variant.id}"
                )
            print("Re-run with --apply to commit and verify.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
