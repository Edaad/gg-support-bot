"""Add do_not_send parking flag on staff_cashout_records.

Usage:
    DATABASE_URL=... python migrate_staff_cashout_do_not_send.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

STATEMENTS = [
    """
    ALTER TABLE staff_cashout_records
    ADD COLUMN IF NOT EXISTS do_not_send BOOLEAN NOT NULL DEFAULT FALSE;
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_staff_cashout_records_do_not_send
    ON staff_cashout_records (do_not_send);
    """,
]


if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        for stmt in STATEMENTS:
            conn.execute(text(stmt))
        conn.commit()
        print("staff_cashout_do_not_send: do_not_send column is ready.")
