"""Add metadata JSONB column to bonus_records.

Usage:
    DATABASE_URL=... python migrate_bonus_records_metadata.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

ALTER = """
ALTER TABLE bonus_records
ADD COLUMN IF NOT EXISTS metadata JSONB;
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(ALTER))
        conn.commit()
        print("bonus_records.metadata is ready.")
