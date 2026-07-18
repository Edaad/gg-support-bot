"""Add analysis columns on transcripts + create group_chat_tickets.

Usage:
    DATABASE_URL=... python migrate_group_chat_tickets.py

Idempotent: safe to run multiple times (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

TRANSCRIPT_COLUMNS = """
ALTER TABLE group_chat_daily_transcripts
    ADD COLUMN IF NOT EXISTS analysis_status VARCHAR(16) NOT NULL DEFAULT 'pending';
ALTER TABLE group_chat_daily_transcripts
    ADD COLUMN IF NOT EXISTS analysis_error TEXT;
ALTER TABLE group_chat_daily_transcripts
    ADD COLUMN IF NOT EXISTS analysis_attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE group_chat_daily_transcripts
    ADD COLUMN IF NOT EXISTS analyzed_at TIMESTAMPTZ;
"""

TRANSCRIPT_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_gcdt_analysis_status
    ON group_chat_daily_transcripts (analysis_status);
CREATE INDEX IF NOT EXISTS ix_gcdt_activity_date_analysis_status
    ON group_chat_daily_transcripts (activity_date, analysis_status);
"""

TICKETS_DDL = """
CREATE TABLE IF NOT EXISTS group_chat_tickets (
    id SERIAL PRIMARY KEY,
    activity_date DATE NOT NULL,
    chat_id BIGINT NOT NULL,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    ticket_index INTEGER NOT NULL,
    start_msg_id BIGINT NOT NULL,
    end_msg_id BIGINT NOT NULL,
    message_ids JSONB NOT NULL,
    brief_summary TEXT,
    category VARCHAR(32) NOT NULL,
    events JSONB,
    summary TEXT,
    prompt_version VARCHAR(32) NOT NULL,
    model VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_gct_activity_date_chat_id_ticket_index
        UNIQUE (activity_date, chat_id, ticket_index)
);
"""

TICKETS_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_gct_club_activity_date
    ON group_chat_tickets (club_id, activity_date);
CREATE INDEX IF NOT EXISTS ix_gct_activity_date
    ON group_chat_tickets (activity_date);
CREATE INDEX IF NOT EXISTS ix_gct_category
    ON group_chat_tickets (category);
"""


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        conn.execute(text(TRANSCRIPT_COLUMNS))
        conn.execute(text(TRANSCRIPT_INDEXES))
        conn.execute(text(TICKETS_DDL))
        conn.execute(text(TICKETS_INDEXES))
    print("group_chat_tickets + transcript analysis columns are ready.")


if __name__ == "__main__":
    main()
