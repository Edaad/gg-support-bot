"""One-time migration: store deposit-sent button message id for strip-on-payment.

Adds:
    escalation_deposit_sent_button_message_id

Usage:
    DATABASE_URL=... python migrate_escalation_deposit_sent_button_message_id.py

Idempotent: safe to run multiple times (ADD COLUMN IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

STATEMENTS = [
    "ALTER TABLE support_group_chats ADD COLUMN IF NOT EXISTS "
    "escalation_deposit_sent_button_message_id BIGINT;",
]

with engine.connect() as conn:
    for stmt in STATEMENTS:
        conn.execute(text(stmt))
    conn.commit()
    print("escalation_deposit_sent_button_message_id column is ready.")
