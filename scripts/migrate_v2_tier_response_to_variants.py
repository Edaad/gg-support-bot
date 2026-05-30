#!/usr/bin/env python3
"""Move legacy tier response_text into Default variants; clear tier copy.

For methods with has_sub_options=false, player copy belongs on variants only.

Usage (dry run):
    python scripts/migrate_v2_tier_response_to_variants.py

Apply:
    python scripts/migrate_v2_tier_response_to_variants.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from sqlalchemy.orm import Session, joinedload

from api.payment_v2_helpers import (
    create_empty_default_variant,
    method_needs_variants,
    migrate_legacy_tier_response_to_variant,
    tier_has_response,
    tier_variant_count,
)
from db.connection import get_session
from db.models import ClubPaymentMethod, ClubPaymentTier


def migrate(session: Session) -> dict[str, int]:
    methods = (
        session.query(ClubPaymentMethod)
        .options(joinedload(ClubPaymentMethod.tiers).joinedload(ClubPaymentTier.variants))
        .all()
    )
    stats = {
        "methods_scanned": 0,
        "tiers_migrated": 0,
        "empty_variants_created": 0,
        "skipped_sub_options": 0,
        "skipped_has_variants": 0,
        "skipped_no_response": 0,
    }

    for method in methods:
        if not method_needs_variants(method):
            stats["skipped_sub_options"] += len(method.tiers)
            continue
        stats["methods_scanned"] += 1
        for tier in method.tiers:
            count = tier_variant_count(session, tier.id)
            if count > 0:
                stats["skipped_has_variants"] += 1
                continue
            if tier_has_response(tier):
                migrate_legacy_tier_response_to_variant(session, tier)
                stats["tiers_migrated"] += 1
            else:
                create_empty_default_variant(session, tier)
                stats["empty_variants_created"] += 1

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit changes")
    args = parser.parse_args()

    from migrate_club_payment_v2 import main as ensure_v2_tables

    ensure_v2_tables()

    session = get_session()
    try:
        stats = migrate(session)
        if args.apply:
            session.commit()
            print("Migration applied:")
        else:
            session.rollback()
            print("Dry run (no changes committed):")
        for key, value in stats.items():
            print(f"  {key}: {value}")
        if not args.apply:
            print("Re-run with --apply to commit.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
