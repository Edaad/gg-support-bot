"""Allow dashboard-created bonus records (nullable admin telegram id).

Usage:
    DATABASE_URL=... python migrate_bonus_records_dashboard.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

STATEMENTS = [
    """
    ALTER TABLE bonus_records
    ALTER COLUMN admin_telegram_user_id DROP NOT NULL;
    """,
]


if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        for stmt in STATEMENTS:
            conn.execute(text(stmt))
        conn.commit()
        print("bonus_records: admin_telegram_user_id is nullable.")
