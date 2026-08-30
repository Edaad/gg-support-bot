"""One-time migration: add the Aces Table join-acknowledgement column.

Adds:
    groups.aces_join_ack_at — set when a Creator Club player confirms they joined
                              Aces Table, so the one-time join link is only shown
                              before their first Aces Table deposit.

Usage:
    DATABASE_URL=... python migrate_aces_join_ack.py

Idempotent: safe to run multiple times (ADD COLUMN IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

STATEMENTS = [
    "ALTER TABLE groups ADD COLUMN IF NOT EXISTS aces_join_ack_at "
    "TIMESTAMPTZ NULL;",
]

with engine.connect() as conn:
    for stmt in STATEMENTS:
        conn.execute(text(stmt))
    conn.commit()
    print("aces_join_ack_at column is ready.")
