"""One-time migration: automated early feeback (/earlyrb) columns + claims table.

Adds:
    clubs.enable_auto_early_rakeback     — per-club toggle for the automated /earlyrb flow
    clubs.early_rakeback_max_auto_amount — remaining above this goes to an admin (NULL = no cap)
    early_rakeback_claims                — one row per automated claim attempt, for reconciliation

The *minimum* claimable amount is not stored here: it comes from Elevate's own
per-club ``earlyRakebackThreshold`` via the bot/quote response.

Usage:
    DATABASE_URL=... python migrate_auto_early_rakeback.py

Idempotent: safe to run multiple times (IF NOT EXISTS everywhere).
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

STATEMENTS = [
    "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS enable_auto_early_rakeback "
    "BOOLEAN NOT NULL DEFAULT FALSE;",
    "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS early_rakeback_max_auto_amount "
    "NUMERIC(12,2);",
    """
    CREATE TABLE IF NOT EXISTS early_rakeback_claims (
        id SERIAL PRIMARY KEY,
        club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
        telegram_chat_id BIGINT NOT NULL,
        telegram_user_id BIGINT,
        group_title TEXT,
        gg_player_id VARCHAR(255) NOT NULL,
        nickname VARCHAR(255),
        union_shorthand VARCHAR(8),
        clubgg_club VARCHAR(64),
        elevate_club_slug VARCHAR(64) NOT NULL,
        rake_overall NUMERIC(14,2),
        rake_filtered NUMERIC(14,2),
        pnl_overall NUMERIC(14,2),
        pnl_filtered NUMERIC(14,2),
        range_start VARCHAR(16),
        range_end VARCHAR(16),
        quoted_amount NUMERIC(14,2),
        recorded_amount NUMERIC(14,2),
        rakeback_percentage NUMERIC(8,4),
        member_type VARCHAR(32),
        warnings JSONB,
        idempotency_key VARCHAR(128) NOT NULL,
        elevate_record_id VARCHAR(64),
        elevate_entry_id VARCHAR(64),
        elevate_total_given NUMERIC(14,2),
        rpa_rake_job_id VARCHAR(64),
        rpa_add_request_id VARCHAR(128),
        chip_add_status VARCHAR(32),
        status VARCHAR(32) NOT NULL DEFAULT 'quoted',
        detail TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW(),
        CONSTRAINT uq_early_rakeback_claims_idempotency_key UNIQUE (idempotency_key)
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_early_rakeback_claims_club_id "
    "ON early_rakeback_claims (club_id);",
    "CREATE INDEX IF NOT EXISTS ix_early_rakeback_claims_chat_id "
    "ON early_rakeback_claims (telegram_chat_id);",
    "CREATE INDEX IF NOT EXISTS ix_early_rakeback_claims_gg_player_id "
    "ON early_rakeback_claims (gg_player_id);",
    "CREATE INDEX IF NOT EXISTS ix_early_rakeback_claims_status "
    "ON early_rakeback_claims (status);",
]

with engine.connect() as conn:
    for stmt in STATEMENTS:
        conn.execute(text(stmt))
    conn.commit()
    print("automated early rakeback columns + early_rakeback_claims are ready.")
