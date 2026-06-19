"""Create migrated_group_recovery table (supergroup migration re-add queue).

Usage:
    DATABASE_URL=... python migrate_migrated_group_recovery.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
PostgreSQL only (JSONB + timestamptz).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS migrated_group_recovery (
    id SERIAL PRIMARY KEY,
    telegram_chat_id BIGINT NOT NULL,
    club_key VARCHAR(64) NOT NULL,
    club_id INTEGER NOT NULL,
    group_title TEXT NOT NULL,
    old_chat_id BIGINT NOT NULL,
    player_telegram_user_id BIGINT,
    player_username TEXT,
    player_display_name TEXT,
    priority_tier SMALLINT NOT NULL,
    priority_rank BIGINT NOT NULL DEFAULT 0,
    readd_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    readd_result JSONB,
    invite_link TEXT,
    last_error TEXT,
    readd_attempted_at TIMESTAMPTZ,
    readd_completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_migrated_group_recovery_telegram_chat_id "
    "ON migrated_group_recovery (telegram_chat_id);",
    "CREATE INDEX IF NOT EXISTS ix_migrated_group_recovery_claim "
    "ON migrated_group_recovery (readd_status, priority_tier, priority_rank);",
    "CREATE INDEX IF NOT EXISTS ix_migrated_group_recovery_club_key "
    "ON migrated_group_recovery (club_key);",
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.commit()
        print("migrated_group_recovery table and indexes are ready.")
