"""One-time migration: add escalation notification toggle per club.

Adds:
    clubs.enable_escalation_notification — player idle ack + Slack escalation

Usage:
    DATABASE_URL=... python migrate_enable_escalation_notification.py

Idempotent: safe to run multiple times (ADD COLUMN IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

STATEMENTS = [
    "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS enable_escalation_notification "
    "BOOLEAN NOT NULL DEFAULT FALSE;",
]

with engine.connect() as conn:
    for stmt in STATEMENTS:
        conn.execute(text(stmt))
    conn.commit()
    print("enable_escalation_notification column is ready.")
