#!/usr/bin/env python
"""List bound payments whose Telegram notification may be out of sync.

Requires payment_binding_events (migrate_payment_binding_events.py). Payments
ingested before that migration will appear here until re-synced or backfilled.

Usage:
    DATABASE_URL=... python scripts/audit_payment_notification_sync.py
    DATABASE_URL=... python scripts/audit_payment_notification_sync.py --method zelle --limit 20
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

from db.connection import get_db, init_engine
from bot.services.payment_binding_events import payments_missing_notification_sync


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--method",
        choices=("venmo", "zelle", "cashapp", "crypto", "all"),
        default="all",
    )
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    init_engine()
    methods = (
        ("venmo", "zelle", "cashapp", "crypto")
        if args.method == "all"
        else (args.method,)
    )
    results: list[dict] = []
    with get_db() as session:
        for method in methods:
            rows = payments_missing_notification_sync(
                session,
                payment_method_slug=method,
                limit=args.limit,
            )
            results.extend(rows)

    print(json.dumps(results, indent=2, default=str))
    print(f"\nTotal: {len(results)} payment(s) missing notification sync proof")


if __name__ == "__main__":
    main()
