"""Add sending flag on staff_cashout_records (pauses 5-minute urgent reminders).

Usage:
    DATABASE_URL=... python migrate_staff_cashout_sending.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

STATEMENTS = [
    """
    ALTER TABLE staff_cashout_records
    ADD COLUMN IF NOT EXISTS sending BOOLEAN NOT NULL DEFAULT FALSE;
    """,
]


if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        for stmt in STATEMENTS:
            conn.execute(text(stmt))
        conn.commit()
        print("staff_cashout_sending: sending column is ready.")
