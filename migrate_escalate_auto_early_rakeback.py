"""One-time migration: Slack staff after a successful auto early-feeback claim.

Adds:
    clubs.escalate_auto_early_rakeback — per-club toggle. On, a successful
    auto-claim also Slack-alerts staff so they can confirm the amount. Off
    (default) = chips land with no success ping. Failures still always alert.

Usage:
    DATABASE_URL=... python migrate_escalate_auto_early_rakeback.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

STATEMENTS = [
    "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS escalate_auto_early_rakeback "
    "BOOLEAN NOT NULL DEFAULT FALSE;",
]

with engine.connect() as conn:
    for stmt in STATEMENTS:
        conn.execute(text(stmt))
    conn.commit()
    print("clubs.escalate_auto_early_rakeback is ready.")
