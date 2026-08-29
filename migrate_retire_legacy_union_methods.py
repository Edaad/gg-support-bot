"""Retire all legacy union (tracks_manual_requests) methods.

Superseded by migrate_union_methods_clean_slate.py for the union method
shape redesign. Use clean-slate wipe + migrate_union_method_shape.py instead.

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
