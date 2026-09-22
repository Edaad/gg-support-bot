"""Add fallback_warned_reason to group_deposit_destination_stickiness.

Usage:
    DATABASE_URL=... python migrate_deposit_destination_stickiness_fallback.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
ALTER TABLE group_deposit_destination_stickiness
ADD COLUMN IF NOT EXISTS fallback_warned_reason TEXT;
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        conn.commit()
        print("group_deposit_destination_stickiness.fallback_warned_reason is ready.")
