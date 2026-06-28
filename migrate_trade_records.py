"""One-time migration: trade record upload tables for audit ingest.

Usage:
    DATABASE_URL=... python migrate_trade_records.py
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

DDL = """
CREATE TABLE IF NOT EXISTS trade_record_uploads (
    id SERIAL PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    audit_date DATE NOT NULL,
    filename VARCHAR(512) NOT NULL,
    metadata_json TEXT,
    replaced_upload_id INTEGER REFERENCES trade_record_uploads(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    CONSTRAINT uq_trade_record_uploads_club_date UNIQUE (club_id, audit_date)
);

CREATE TABLE IF NOT EXISTS trade_record_lines (
    id SERIAL PRIMARY KEY,
    upload_id INTEGER NOT NULL REFERENCES trade_record_uploads(id) ON DELETE CASCADE,
    sheet_row INTEGER NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE,
    amount NUMERIC(14, 2) NOT NULL,
    member_gg_player_id VARCHAR(255),
    member_nickname VARCHAR(255),
    agent_gg_player_id VARCHAR(255),
    super_agent_gg_player_id VARCHAR(255)
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_trade_record_uploads_club_id ON trade_record_uploads (club_id);",
    "CREATE INDEX IF NOT EXISTS ix_trade_record_lines_upload_id ON trade_record_lines (upload_id);",
    "CREATE INDEX IF NOT EXISTS ix_trade_record_lines_member_gg_player_id ON trade_record_lines (member_gg_player_id);",
]

with engine.connect() as conn:
    conn.execute(text(DDL))
    for stmt in INDEXES:
        conn.execute(text(stmt))
    conn.commit()
    print("trade_record_uploads and trade_record_lines are ready.")
