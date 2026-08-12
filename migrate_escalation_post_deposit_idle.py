"""One-time migration: post-deposit immediate idle arm flag.

Adds:
    escalation_post_deposit_idle_pending

Usage:
    DATABASE_URL=... python migrate_escalation_post_deposit_idle.py

Idempotent: safe to run multiple times (ADD COLUMN IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

STATEMENTS = [
    "ALTER TABLE support_group_chats ADD COLUMN IF NOT EXISTS "
    "escalation_post_deposit_idle_pending BOOLEAN NOT NULL DEFAULT FALSE;",
]

with engine.connect() as conn:
    for stmt in STATEMENTS:
        conn.execute(text(stmt))
    conn.commit()
    print("escalation_post_deposit_idle_pending column is ready.")
