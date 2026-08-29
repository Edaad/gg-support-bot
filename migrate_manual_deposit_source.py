"""Add source column to manual_deposit_requests (bot vs dashboard).

Usage:
    DATABASE_URL=... python migrate_manual_deposit_source.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

COLUMN_DDL = """
ALTER TABLE manual_deposit_requests
ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'bot'
"""


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        conn.execute(text(COLUMN_DDL))
    print("migrate_manual_deposit_source: done")


if __name__ == "__main__":
    main()
