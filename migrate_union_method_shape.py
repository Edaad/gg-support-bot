"""Add union method shape columns; drop manual_request_message.

Run after migrate_union_methods_clean_slate.py on a clean union slate.

Usage:
    DATABASE_URL=... python migrate_union_method_shape.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

STMTS = [
    """
    ALTER TABLE club_payment_methods
    ADD COLUMN IF NOT EXISTS union_type VARCHAR(20)
    """,
    """
    ALTER TABLE club_payment_methods
    ADD COLUMN IF NOT EXISTS deposit_union VARCHAR(20)
    """,
    """
    ALTER TABLE club_payment_methods
    ADD COLUMN IF NOT EXISTS method_tag VARCHAR(200)
    """,
    """
    ALTER TABLE club_payment_methods
    ADD COLUMN IF NOT EXISTS payment_account_name VARCHAR(200)
    """,
    """
    ALTER TABLE club_payment_methods
    DROP COLUMN IF EXISTS manual_request_message
    """,
]


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        for stmt in STMTS:
            conn.execute(text(stmt))
    print("migrate_union_method_shape: done")


if __name__ == "__main__":
    main()
