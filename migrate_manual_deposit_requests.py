"""Add manual trade-request fields and manual_deposit_requests table.

Usage:
    DATABASE_URL=... python migrate_manual_deposit_requests.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

COLUMN_DDL = [
    """
    ALTER TABLE club_payment_methods
    ADD COLUMN IF NOT EXISTS tracks_manual_requests BOOLEAN NOT NULL DEFAULT false
    """,
    """
    ALTER TABLE club_payment_methods
    ADD COLUMN IF NOT EXISTS manual_request_message TEXT
    """,
    """
    ALTER TABLE club_payment_methods
    ADD COLUMN IF NOT EXISTS manual_request_variant_name VARCHAR(100)
    """,
]

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS manual_deposit_requests (
    id SERIAL PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    method_id INTEGER REFERENCES club_payment_methods(id) ON DELETE SET NULL,
    method_name VARCHAR(50) NOT NULL,
    method_slug VARCHAR(50) NOT NULL,
    variant_name VARCHAR(100) NOT NULL,
    group_title VARCHAR(512),
    amount NUMERIC(12, 2) NOT NULL,
    telegram_chat_id BIGINT NOT NULL,
    trade_record_checked BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS ix_mdr_method_id
    ON manual_deposit_requests (method_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_mdr_club_created
    ON manual_deposit_requests (club_id, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_mdr_checked_created
    ON manual_deposit_requests (trade_record_checked, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_mdr_method_slug
    ON manual_deposit_requests (method_slug)
    """,
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        for stmt in COLUMN_DDL:
            conn.execute(text(stmt))
        conn.execute(text(TABLE_DDL))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.commit()
        print("manual_deposit_requests is ready.")
