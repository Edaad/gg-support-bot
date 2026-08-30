"""One-time migration: add the Aces Table picker deposit threshold.

Adds:
    clubs.aces_option_min_deposits — Creator Club only. Deposits a support group
                                     must already have before the Creator Club /
                                     Aces Table picker is offered. 0 = always.

Usage:
    DATABASE_URL=... python migrate_aces_option_min_deposits.py

Idempotent: safe to run multiple times (ADD COLUMN IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

STATEMENTS = [
    "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS aces_option_min_deposits "
    "INTEGER NOT NULL DEFAULT 0;",
]

with engine.connect() as conn:
    for stmt in STATEMENTS:
        conn.execute(text(stmt))
    conn.commit()
    print("aces_option_min_deposits column is ready.")
