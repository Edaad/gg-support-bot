"""Create group_photo_backfill_rows for the hourly RT/CC photo job.

Usage:
    DATABASE_URL=... python migrate_group_photo_backfill.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
The worker also creates this table via SQLAlchemy ``Base.metadata.create_all``.
PostgreSQL only (timestamptz).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_ROWS = """
CREATE TABLE IF NOT EXISTS group_photo_backfill_rows (
    id SERIAL PRIMARY KEY,
    club_key VARCHAR(64) NOT NULL,
    telegram_chat_id BIGINT NOT NULL,
    support_group_chat_id INTEGER,
    group_title TEXT,
    status VARCHAR(32) NOT NULL,
    error TEXT,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_group_photo_backfill_club_chat UNIQUE (club_key, telegram_chat_id)
);
"""

DDL_INDEX = """
CREATE INDEX IF NOT EXISTS ix_group_photo_backfill_status
    ON group_photo_backfill_rows (status);
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL_ROWS))
        conn.execute(text(DDL_INDEX))
        conn.commit()
        print("group_photo_backfill_rows is ready.")
