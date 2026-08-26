"""Retire all legacy union (tracks_manual_requests) methods.

Run once after deploying the union-method redesign. Ops then creates new
methods with Method + Tag in the dashboard.

Usage:
    DATABASE_URL=... python migrate_retire_legacy_union_methods.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

RETIRE = """
UPDATE club_payment_methods
SET is_active = false
WHERE tracks_manual_requests = true
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.begin() as conn:
        result = conn.execute(text(RETIRE))
        print(f"Retired {result.rowcount} union method(s).")
