#!/usr/bin/env python3
"""Seed Creator Club deposit Crypto into greenfield club_payment_* tables.

Idempotent upserts by (club_id, direction, slug), (method_id, tier label),
and (method_id, sub-option slug). Player-facing copy lives on sub-options only.

Usage (dry run — no writes):
    python scripts/seed_v2_creator_club_crypto.py

Apply + verify via /api/v2:
    python scripts/seed_v2_creator_club_crypto.py --apply
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
from db.models import (
    Club,
    ClubPaymentMethod,
    ClubPaymentSubOption,
    ClubPaymentTier,
)

CLUB_NAME = "Creator Club"
METHOD_SLUG = "crypto"
DIRECTION = "deposit"
DEFAULT_TIER_LABEL = "Default"
ACCUMULATED_AMOUNT = Decimal("4284.00")

_FOOTER = """• Once sent, please inform us, and an agent will confirm the transaction and add your chips within 2 minutes!

You can send payment to this address anytime in the future. Just post a screenshot of your latest transaction, and it will be credited to your account!"""


def _crypto_response(header: str, address: str) -> str:
    return f"{header}\n---\n{address}\n---\n{_FOOTER}"


SUB_OPTIONS: list[dict[str, str]] = [
    {
        "name": "Ethereum",
        "slug": "eth",
        "response_text": _crypto_response(
            "Ethereum (ETH) Address: ",
            "0xc50b01cAa68EcB657807b198E1C3360fAC3CBA96",
        ),
    },
    {
        "name": "USDT ERC20",
        "slug": "usdterc20",
        "response_text": _crypto_response(
            "USDT ERC20 Address: ",
            "0xc50b01cAa68EcB657807b198E1C3360fAC3CBA96",
        ),
    },
    {
        "name": "USDC ERC20",
        "slug": "usdcerc20",
        "response_text": _crypto_response(
            "USDC ERC20 Address: ",
            "0xc50b01cAa68EcB657807b198E1C3360fAC3CBA96",
        ),
    },
    {
        "name": "Bitcoin",
        "slug": "btc",
        "response_text": _crypto_response(
            "Bitcoin (BTC) Address: ",
            "bc1qsrg6lmaflt0hecvqjjvugxe9t3r4tfzapk72zd",
        ),
    },
    {
        "name": "Solana",
        "slug": "sol",
        "response_text": _crypto_response(
            "Solana (SOL) Address: ",
            "BZYb4e4x2pgmz2U9YoyCzWXzNgqoGZ4E9PZksHEMktqH",
        ),
    },
    {
        "name": "USDT SOL",
        "slug": "usdtsol",
        "response_text": _crypto_response(
            "USDT SOL Address: ",
            "BZYb4e4x2pgmz2U9YoyCzWXzNgqoGZ4E9PZksHEMktqH",
        ),
    },
    {
        "name": "USDC SOL",
        "slug": "usdcsol",
        "response_text": _crypto_response(
            "USDC SOL Address: ",
            "BZYb4e4x2pgmz2U9YoyCzWXzNgqoGZ4E9PZksHEMktqH",
        ),
    },
    {
        "name": "Tron",
        "slug": "trx",
        "response_text": _crypto_response(
            "Tron (TRX) Address: ",
            "TTswLWyB8WRb9ZPHiZ1zXsmAjeh3ux4Som",
        ),
    },
    {
        "name": "USDT TRC20",
        "slug": "usdttrc20",
        "response_text": _crypto_response(
            "USDT TRC20 Address: ",
            "TTswLWyB8WRb9ZPHiZ1zXsmAjeh3ux4Som",
        ),
    },
    {
        "name": "XRP",
        "slug": "xrp",
        "response_text": _crypto_response(
            "XRP Address: ",
            "rsjwQY4yS3iXSvkB4sMEiC1uMENLXkg42C",
        ),
    },
    {
        "name": "Litecoin",
        "slug": "ltc",
        "response_text": _crypto_response(
            "Litecoin (LTC) Address: ",
            "ltc1q8yc48p4979ql2l0c0ch6u06fjeveya4a2e4m7g",
        ),
    },
]

EXPECTED_SUB_SLUGS = {row["slug"] for row in SUB_OPTIONS}


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

    method.name = "Crypto"
    method.min_amount = Decimal("20")
    method.max_amount = None
    method.has_sub_options = True
    method.is_active = True
    method.sort_order = 0
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


def upsert_sub_options(session: Session, method_id: int) -> list[ClubPaymentSubOption]:
    rows: list[ClubPaymentSubOption] = []
    for sort_order, spec in enumerate(SUB_OPTIONS):
        sub = (
            session.query(ClubPaymentSubOption)
            .filter_by(method_id=method_id, slug=spec["slug"])
            .first()
        )
        if sub is None:
            sub = ClubPaymentSubOption(method_id=method_id, slug=spec["slug"])
            session.add(sub)

        sub.name = spec["name"]
        sub.response_type = "text"
        sub.response_text = spec["response_text"]
        sub.response_file_id = None
        sub.response_caption = None
        sub.is_active = True
        sub.sort_order = sort_order
        rows.append(sub)

    session.flush()
    return rows


def seed(session: Session) -> tuple[Club, ClubPaymentMethod, ClubPaymentTier, list[ClubPaymentSubOption]]:
    club = find_creator_club(session)
    method = upsert_method(session, club.id)
    tier = upsert_default_tier(session, method.id)
    subs = upsert_sub_options(session, method.id)
    return club, method, tier, subs


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
    crypto = [m for m in methods if m.get("slug") == METHOD_SLUG]
    if len(crypto) != 1:
        raise SystemExit(f"Expected exactly one crypto method, got {len(crypto)}")

    method = crypto[0]
    if not method.get("has_sub_options"):
        raise SystemExit("Expected has_sub_options=true on Crypto method")

    if Decimal(str(method.get("min_amount"))) != Decimal("20"):
        raise SystemExit(f"Expected method min_amount 20, got {method.get('min_amount')!r}")

    tiers = method.get("tiers") or []
    if len(tiers) != 1:
        raise SystemExit(f"Expected 1 tier, got {len(tiers)}")
    if tiers[0].get("label") != DEFAULT_TIER_LABEL:
        raise SystemExit(f"Expected tier label {DEFAULT_TIER_LABEL!r}, got {tiers[0].get('label')!r}")

    if (tiers[0].get("response_text") or "").strip():
        raise SystemExit("Expected empty tier response_text; copy lives on sub-options")

    for tier in tiers:
        variants = tier.get("variants") or []
        if variants:
            raise SystemExit(f"Expected 0 variants on tier {tier.get('label')!r}, got {len(variants)}")

    subs = method.get("sub_options") or []
    if len(subs) != 11:
        raise SystemExit(f"Expected 11 sub-options, got {len(subs)}")

    slugs = {s.get("slug") for s in subs}
    if slugs != EXPECTED_SUB_SLUGS:
        missing = EXPECTED_SUB_SLUGS - slugs
        extra = slugs - EXPECTED_SUB_SLUGS
        raise SystemExit(f"Sub-option slug mismatch: missing={missing}, extra={extra}")

    for sub in subs:
        if not sub.get("is_active"):
            raise SystemExit(f"Expected sub-option {sub.get('slug')!r} to be active")
        if not (sub.get("response_text") or "").strip():
            raise SystemExit(f"Expected non-empty response_text on {sub.get('slug')!r}")

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
        club, method, tier, subs = seed(session)
        if args.apply:
            session.commit()
            print(
                f"Seeded Creator Club Crypto (v2): club_id={club.id}, method_id={method.id}, "
                f"tier_id={tier.id}, sub_options={len(subs)}"
            )
            verify_via_api(club.id)
            print("API verification passed: 1 Default tier, 11 sub-options, 0 variants.")
        else:
            session.rollback()
            print("Dry run (no changes committed). Would upsert:")
            print(f"  club: {club.name!r} (id={club.id})")
            print(
                f"  method: Crypto / {METHOD_SLUG} (deposit, min=$20, "
                f"accumulated=${ACCUMULATED_AMOUNT:,.2f}, sort_order=0)"
            )
            print(f"  tier: {DEFAULT_TIER_LABEL!r} (min=$20, no response, no Stripe)")
            print(f"  sub-options: {len(subs)} ({', '.join(row['slug'] for row in SUB_OPTIONS)})")
            print("  variants: 0")
            print("Re-run with --apply to commit and verify.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
