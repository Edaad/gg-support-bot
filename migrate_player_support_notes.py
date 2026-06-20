"""Create player_support_issues and player_support_notes tables.

Usage:
    DATABASE_URL=... python migrate_player_support_notes.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_ISSUES = """
CREATE TABLE IF NOT EXISTS player_support_issues (
    id SERIAL PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    gg_player_id VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'open',
    telegram_chat_id BIGINT,
    resolved_at TIMESTAMPTZ,
    resolved_by_telegram_user_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

DDL_NOTES = """
CREATE TABLE IF NOT EXISTS player_support_notes (
    id SERIAL PRIMARY KEY,
    issue_id INTEGER NOT NULL REFERENCES player_support_issues(id) ON DELETE CASCADE,
    situation TEXT NOT NULL,
    actions_taken TEXT NOT NULL,
    next_steps TEXT NOT NULL,
    created_by_telegram_user_id BIGINT NOT NULL,
    source_telegram_chat_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS ix_player_support_issues_club_id
    ON player_support_issues (club_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_player_support_issues_gg_player_id
    ON player_support_issues (gg_player_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_player_support_issues_status
    ON player_support_issues (status);
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_player_support_issues_open_club_player
    ON player_support_issues (club_id, gg_player_id)
    WHERE status = 'open';
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_player_support_notes_issue_id
    ON player_support_notes (issue_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_player_support_notes_created_at
    ON player_support_notes (created_at);
    """,
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL_ISSUES))
        conn.execute(text(DDL_NOTES))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.commit()
        print("player_support_issues and player_support_notes are ready.")
