"""One-time migration: add the /transfer feature flag.

Adds:
    clubs.enable_transfer — when on, players in a club with two unions can move
                            chips between them with /transfer (claim from the
                            source union, add to the destination union).

Usage:
    DATABASE_URL=... python migrate_enable_transfer.py

Idempotent: safe to run multiple times (ADD COLUMN IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

STATEMENTS = [
    "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS enable_transfer "
    "BOOLEAN NOT NULL DEFAULT false;",
]

with engine.connect() as conn:
    for stmt in STATEMENTS:
        conn.execute(text(stmt))
    conn.commit()
    print("enable_transfer column is ready.")
