"""One-time migration: durable escalation activity state on support_group_chats.

Adds:
    escalation_last_human_at
    escalation_last_human_role
    escalation_idle_episode_fired
    escalation_deposit_instructions_pending
    escalation_deposit_method_slug
    escalation_deposit_sent_armed_at

Usage:
    DATABASE_URL=... python migrate_escalation_activity_state.py

Idempotent: safe to run multiple times (ADD COLUMN IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

STATEMENTS = [
    "ALTER TABLE support_group_chats ADD COLUMN IF NOT EXISTS "
    "escalation_last_human_at TIMESTAMPTZ;",
    "ALTER TABLE support_group_chats ADD COLUMN IF NOT EXISTS "
    "escalation_last_human_role VARCHAR(16);",
    "ALTER TABLE support_group_chats ADD COLUMN IF NOT EXISTS "
    "escalation_idle_episode_fired BOOLEAN NOT NULL DEFAULT FALSE;",
    "ALTER TABLE support_group_chats ADD COLUMN IF NOT EXISTS "
    "escalation_deposit_instructions_pending BOOLEAN NOT NULL DEFAULT FALSE;",
    "ALTER TABLE support_group_chats ADD COLUMN IF NOT EXISTS "
    "escalation_deposit_method_slug VARCHAR(64);",
    "ALTER TABLE support_group_chats ADD COLUMN IF NOT EXISTS "
    "escalation_deposit_sent_armed_at TIMESTAMPTZ;",
]

with engine.connect() as conn:
    for stmt in STATEMENTS:
        conn.execute(text(stmt))
    conn.commit()
    print("escalation activity state columns are ready.")
