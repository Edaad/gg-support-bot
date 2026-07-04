"""Create bonus_drafts table.

Usage:
    DATABASE_URL=... python migrate_bonus_drafts.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS bonus_drafts (
    id SERIAL PRIMARY KEY,
    staff_telegram_user_id BIGINT NOT NULL,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    group_title VARCHAR(512),
    telegram_chat_id BIGINT,
    player_username VARCHAR(255),
    amount NUMERIC(12, 2) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
"""

INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS ix_bonus_drafts_staff_user_id
    ON bonus_drafts (staff_telegram_user_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_bonus_drafts_status
    ON bonus_drafts (status);
    """,
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.commit()
        print("bonus_drafts is ready.")
