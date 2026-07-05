"""One-time migration: add payment auto-deposit toggle per club.

Adds:
    clubs.auto_deposit_on_payment_enabled — e2e chip-add on auto-bound payment receipt

Usage:
    DATABASE_URL=... python migrate_auto_deposit_on_payment.py

Idempotent: safe to run multiple times (ADD COLUMN IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

STATEMENTS = [
    "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS auto_deposit_on_payment_enabled "
    "BOOLEAN NOT NULL DEFAULT FALSE;",
]

with engine.connect() as conn:
    for stmt in STATEMENTS:
        conn.execute(text(stmt))
    conn.commit()
    print("auto_deposit_on_payment_enabled column is ready.")
