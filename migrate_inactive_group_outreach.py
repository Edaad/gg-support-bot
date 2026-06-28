"""Create inactive group outreach scan tables (control singleton + audit rows).

Usage:
    DATABASE_URL=... python migrate_inactive_group_outreach.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
PostgreSQL only (timestamptz).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_CONTROL = """
CREATE TABLE IF NOT EXISTS inactive_group_outreach_control (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    scan_status VARCHAR(32) NOT NULL DEFAULT 'idle',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    targets_total INTEGER NOT NULL DEFAULT 0,
    rows_scanned INTEGER NOT NULL DEFAULT 0,
    inactive_90d_count INTEGER NOT NULL DEFAULT 0,
    inactive_180d_count INTEGER NOT NULL DEFAULT 0,
    entity_resolvable_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_tick_at TIMESTAMPTZ
);
"""

DDL_ROWS = """
CREATE TABLE IF NOT EXISTS inactive_group_outreach_rows (
    id SERIAL PRIMARY KEY,
    club_key VARCHAR(64) NOT NULL,
    telegram_chat_id BIGINT NOT NULL,
    group_title TEXT NOT NULL,
    legacy_chat_id BIGINT,
    gg_player_id VARCHAR(64),
    last_external_message_at TIMESTAMPTZ,
    activity_basis VARCHAR(32),
    last_external_supergroup_at TIMESTAMPTZ,
    activity_basis_supergroup VARCHAR(32),
    last_external_legacy_at TIMESTAMPTZ,
    activity_basis_legacy VARCHAR(32),
    activity_merged_from VARCHAR(16),
    inactive_90d BOOLEAN NOT NULL DEFAULT FALSE,
    inactive_180d BOOLEAN NOT NULL DEFAULT FALSE,
    duplicate_title BOOLEAN NOT NULL DEFAULT FALSE,
    newer_same_title_chat_id BIGINT,
    player_telegram_user_id BIGINT,
    player_username TEXT,
    player_display_name TEXT,
    player_source VARCHAR(32),
    account_check VARCHAR(16),
    entity_resolvable BOOLEAN NOT NULL DEFAULT FALSE,
    scan_status VARCHAR(16) NOT NULL DEFAULT 'pending',
    scan_error TEXT,
    scanned_at TIMESTAMPTZ,
    dm_status VARCHAR(32),
    dm_error TEXT,
    dm_sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_inactive_group_outreach_club_chat UNIQUE (club_key, telegram_chat_id)
);
"""

DDL_ROWS_INDEX = """
CREATE INDEX IF NOT EXISTS ix_inactive_group_outreach_rows_scan_status
    ON inactive_group_outreach_rows (scan_status, club_key);
"""

SEED = """
INSERT INTO inactive_group_outreach_control (id)
VALUES (1)
ON CONFLICT (id) DO NOTHING;
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL_CONTROL))
        conn.execute(text(DDL_ROWS))
        conn.execute(text(DDL_ROWS_INDEX))
        conn.execute(text(SEED))
        conn.commit()
        print("inactive_group_outreach tables are ready.")
