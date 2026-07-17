"""Create group_chat_daily_transcripts table for nightly conversation extracts.

Usage:
    DATABASE_URL=... python migrate_group_chat_daily_transcripts.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS group_chat_daily_transcripts (
    id SERIAL PRIMARY KEY,
    activity_date DATE NOT NULL,
    chat_id BIGINT NOT NULL,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    message_count INTEGER NOT NULL DEFAULT 0,
    messages JSONB,
    error TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    fetched_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_gcdt_activity_date_chat_id UNIQUE (activity_date, chat_id)
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_gcdt_club_activity_date
    ON group_chat_daily_transcripts (club_id, activity_date);
CREATE INDEX IF NOT EXISTS ix_gcdt_activity_date
    ON group_chat_daily_transcripts (activity_date);
CREATE INDEX IF NOT EXISTS ix_gcdt_status
    ON group_chat_daily_transcripts (status);
"""


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        conn.execute(text(DDL))
        conn.execute(text(INDEXES))
    print("group_chat_daily_transcripts table is ready.")


if __name__ == "__main__":
    main()
