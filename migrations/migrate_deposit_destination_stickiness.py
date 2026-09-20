"""Create group_deposit_destination_stickiness table.

Usage:
    DATABASE_URL=... python migrate_deposit_destination_stickiness.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS group_deposit_destination_stickiness (
    id SERIAL PRIMARY KEY,
    telegram_chat_id BIGINT NOT NULL,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    payment_method_slug VARCHAR(32) NOT NULL,
    destination_tag VARCHAR(100) NOT NULL,
    variant_id INTEGER REFERENCES club_payment_tier_variants(id) ON DELETE SET NULL,
    shown_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_gdds_chat_method
        UNIQUE (telegram_chat_id, payment_method_slug)
);
"""

INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS ix_gdds_telegram_chat_id
    ON group_deposit_destination_stickiness (telegram_chat_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_gdds_club_slug
    ON group_deposit_destination_stickiness (club_id, payment_method_slug);
    """,
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.commit()
        print("group_deposit_destination_stickiness is ready.")
