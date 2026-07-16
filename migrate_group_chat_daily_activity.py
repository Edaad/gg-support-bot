"""Create group_chat_daily_activity table for daily support-group activity rollups.

Usage:
    DATABASE_URL=... python migrate_group_chat_daily_activity.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS group_chat_daily_activity (
    id SERIAL PRIMARY KEY,
    activity_date DATE NOT NULL,
    chat_id BIGINT NOT NULL,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    non_bot_message_count INTEGER NOT NULL DEFAULT 1,
    first_message_at TIMESTAMPTZ NOT NULL,
    last_message_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_gcda_activity_date_chat_id UNIQUE (activity_date, chat_id)
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_gcda_club_activity_date
    ON group_chat_daily_activity (club_id, activity_date);
CREATE INDEX IF NOT EXISTS ix_gcda_activity_date
    ON group_chat_daily_activity (activity_date);
"""


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        conn.execute(text(DDL))
        conn.execute(text(INDEXES))
    print("group_chat_daily_activity table is ready.")


if __name__ == "__main__":
    main()
