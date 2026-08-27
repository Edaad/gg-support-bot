"""One-time migration: add the automated-cashout toggle column.

Adds:
    clubs.enable_auto_cashout — per-club toggle for the fully automated player
                                /cashout flow (claim chips, collect payout handle,
                                record to the hub automatically).

Usage:
    DATABASE_URL=... python migrate_enable_auto_cashout.py

Idempotent: safe to run multiple times (ADD COLUMN IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

STATEMENTS = [
    "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS enable_auto_cashout "
    "BOOLEAN NOT NULL DEFAULT FALSE;",
]

with engine.connect() as conn:
    for stmt in STATEMENTS:
        conn.execute(text(stmt))
    conn.commit()
    print("enable_auto_cashout column is ready.")
