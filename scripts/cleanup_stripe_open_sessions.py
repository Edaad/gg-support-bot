"""Remove unpaid open checkout rows (legacy). Completed payments are webhook-only now.

Usage:
    DATABASE_URL=... python scripts/cleanup_stripe_open_sessions.py
    DATABASE_URL=... python scripts/cleanup_stripe_open_sessions.py --apply
"""

from __future__ import annotations

import argparse

from sqlalchemy import text

from db.connection import init_engine


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Execute DELETE (default is dry-run)")
    args = parser.parse_args()

    engine = init_engine()
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM stripe_checkout_sessions WHERE status != 'complete'")
        ).scalar()
        print(f"Rows to delete (status != complete): {count}")
        if args.apply and count:
            conn.execute(text("DELETE FROM stripe_checkout_sessions WHERE status != 'complete'"))
            conn.commit()
            print("Deleted.")
        elif not args.apply:
            print("Dry run. Pass --apply to delete.")


if __name__ == "__main__":
    main()
