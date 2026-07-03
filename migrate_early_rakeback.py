"""One-time migration: early rakeback snapshot tables for audit sync.

Usage:
    DATABASE_URL=... python migrate_early_rakeback.py
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

DDL = """
CREATE TABLE IF NOT EXISTS early_rakeback_snapshots (
    id SERIAL PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    club_slug VARCHAR(64) NOT NULL,
    audit_date DATE NOT NULL,
    fetch_from_utc TIMESTAMP WITH TIME ZONE NOT NULL,
    fetch_to_utc TIMESTAMP WITH TIME ZONE NOT NULL,
    lines_fetched INTEGER NOT NULL DEFAULT 0,
    lines_stored INTEGER NOT NULL DEFAULT 0,
    lines_skipped_unmapped INTEGER NOT NULL DEFAULT 0,
    skipped_nicknames TEXT,
    synced_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    CONSTRAINT uq_early_rakeback_snapshots_slug_date UNIQUE (club_slug, audit_date)
);

CREATE TABLE IF NOT EXISTS early_rakeback_lines (
    id SERIAL PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES early_rakeback_snapshots(id) ON DELETE CASCADE,
    source_entry_id VARCHAR(64) NOT NULL,
    source_record_id VARCHAR(64) NOT NULL,
    gg_player_id VARCHAR(255) NOT NULL,
    member_nickname VARCHAR(255),
    member_type VARCHAR(32),
    amount_usd NUMERIC(14, 2) NOT NULL,
    rake NUMERIC(14, 2),
    pl NUMERIC(14, 2),
    rakeback_percentage NUMERIC(8, 4),
    occurred_at TIMESTAMP WITH TIME ZONE
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_early_rakeback_snapshots_club_id ON early_rakeback_snapshots (club_id);",
    "CREATE INDEX IF NOT EXISTS ix_early_rakeback_snapshots_club_slug ON early_rakeback_snapshots (club_slug);",
    "CREATE INDEX IF NOT EXISTS ix_early_rakeback_lines_snapshot_id ON early_rakeback_lines (snapshot_id);",
    "CREATE INDEX IF NOT EXISTS ix_early_rakeback_lines_gg_player_id ON early_rakeback_lines (gg_player_id);",
]

with engine.connect() as conn:
    conn.execute(text(DDL))
    for stmt in INDEXES:
        conn.execute(text(stmt))
    conn.commit()
    print("early_rakeback_snapshots and early_rakeback_lines are ready.")
