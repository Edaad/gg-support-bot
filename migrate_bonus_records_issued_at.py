"""Add issued_at to bonus_records (when the bonus was issued).

Backfills issued_at from created_at for existing rows.

Usage:
    DATABASE_URL=... python migrate_bonus_records_issued_at.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

STATEMENTS = [
    """
    ALTER TABLE bonus_records
    ADD COLUMN IF NOT EXISTS issued_at TIMESTAMP
    """,
    """
    UPDATE bonus_records
    SET issued_at = COALESCE(created_at, NOW())
    WHERE issued_at IS NULL
    """,
    """
    ALTER TABLE bonus_records
    ALTER COLUMN issued_at SET DEFAULT NOW()
    """,
    """
    ALTER TABLE bonus_records
    ALTER COLUMN issued_at SET NOT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_bonus_records_issued_at
    ON bonus_records (issued_at)
    """,
]


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        for stmt in STATEMENTS:
            conn.execute(text(stmt))
    print("migrate_bonus_records_issued_at: done")


if __name__ == "__main__":
    main()
