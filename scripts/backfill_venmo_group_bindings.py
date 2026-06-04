#!/usr/bin/env python3
"""Backfill group_payment_method_bindings from bound venmo_payments rows.

Usage:
    DATABASE_URL=... python scripts/backfill_venmo_group_bindings.py --dry-run
    DATABASE_URL=... python scripts/backfill_venmo_group_bindings.py --apply
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

from bot.services.payment_method_binding import (
    BOUND_VIA_BACKFILL,
    infer_variant_id_for_venmo_handle,
    record_group_binding,
)
from db.connection import get_db
from db.models import GroupPaymentMethodBinding, VenmoPayment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write bindings (default is dry-run)",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    with get_db() as session:
        rows = (
            session.query(
                VenmoPayment.telegram_chat_id,
                VenmoPayment.club_id,
                VenmoPayment.venmo_handle,
            )
            .filter(
                VenmoPayment.telegram_chat_id.isnot(None),
                VenmoPayment.bound_at.isnot(None),
                VenmoPayment.club_id.isnot(None),
            )
            .group_by(
                VenmoPayment.telegram_chat_id,
                VenmoPayment.club_id,
                VenmoPayment.venmo_handle,
            )
            .all()
        )

    created = 0
    skipped = 0
    for chat_id, club_id, handle in rows:
        if chat_id is None or club_id is None:
            skipped += 1
            continue
        with get_db() as session:
            exists = (
                session.query(GroupPaymentMethodBinding.id)
                .filter_by(
                    telegram_chat_id=int(chat_id),
                    payment_method_slug="venmo",
                )
                .one_or_none()
            )
        if exists is not None:
            skipped += 1
            continue

        variant_id = infer_variant_id_for_venmo_handle(int(club_id), str(handle))
        if dry_run:
            print(
                f"would bind chat_id={chat_id} club_id={club_id} "
                f"handle={handle!r} variant_id={variant_id}"
            )
            created += 1
            continue

        record_group_binding(
            telegram_chat_id=int(chat_id),
            club_id=int(club_id),
            payment_method_slug="venmo",
            bound_via=BOUND_VIA_BACKFILL,
            variant_id=variant_id,
            venmo_handle=str(handle),
        )
        created += 1

    mode = "dry-run" if dry_run else "apply"
    print(f"{mode}: created={created} skipped_existing={skipped}")


if __name__ == "__main__":
    main()
