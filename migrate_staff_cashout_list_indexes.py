"""Composite indexes for the cashout records list (do_not_send + created_at).

Replaces the single-column do_not_send index: (do_not_send, created_at) covers
the same equality filter and matches ORDER BY created_at DESC.

Usage:
    DATABASE_URL=... python migrate_staff_cashout_list_indexes.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

STATEMENTS = [
    """
    CREATE INDEX IF NOT EXISTS ix_staff_cashout_records_do_not_send_created_at
    ON staff_cashout_records (do_not_send, created_at);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_staff_cashout_records_do_not_send_club_created_at
    ON staff_cashout_records (do_not_send, club_id, created_at);
    """,
    """
    DROP INDEX IF EXISTS ix_staff_cashout_records_do_not_send;
    """,
]


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        for stmt in STATEMENTS:
            conn.execute(text(stmt))
    print("staff_cashout_list_indexes: do_not_send + created_at indexes are ready.")


if __name__ == "__main__":
    main()
