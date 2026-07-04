"""One-time migration: audit reconcile run persistence.

Usage:
    DATABASE_URL=... python migrate_audit_reconcile.py
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

DDL = """
CREATE TABLE IF NOT EXISTS audit_reconcile_runs (
    id SERIAL PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    club_slug VARCHAR(64) NOT NULL,
    audit_date DATE NOT NULL,
    status VARCHAR(16) NOT NULL,
    trade_upload_id INTEGER REFERENCES trade_record_uploads(id) ON DELETE SET NULL,
    early_rb_snapshot_id INTEGER REFERENCES early_rakeback_snapshots(id) ON DELETE SET NULL,
    players_matched INTEGER NOT NULL DEFAULT 0,
    players_failed INTEGER NOT NULL DEFAULT 0,
    unmatched_trade_count INTEGER NOT NULL DEFAULT 0,
    unmatched_ledger_count INTEGER NOT NULL DEFAULT 0,
    report_json TEXT NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    CONSTRAINT uq_audit_reconcile_runs_slug_date UNIQUE (club_slug, audit_date)
);

CREATE TABLE IF NOT EXISTS glide_audit_lines (
    id SERIAL PRIMARY KEY,
    club_slug VARCHAR(64) NOT NULL,
    audit_date DATE NOT NULL,
    glide_row_id VARCHAR(128) NOT NULL,
    gg_player_id VARCHAR(255),
    amount_usd NUMERIC(14, 2) NOT NULL,
    event_type VARCHAR(64),
    occurred_at TIMESTAMP WITH TIME ZONE,
    raw_json TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_audit_reconcile_runs_club_id ON audit_reconcile_runs (club_id);",
    "CREATE INDEX IF NOT EXISTS ix_audit_reconcile_runs_club_slug ON audit_reconcile_runs (club_slug);",
    "CREATE INDEX IF NOT EXISTS ix_audit_reconcile_runs_audit_date ON audit_reconcile_runs (audit_date);",
    "CREATE INDEX IF NOT EXISTS ix_glide_audit_lines_club_slug ON glide_audit_lines (club_slug);",
    "CREATE INDEX IF NOT EXISTS ix_glide_audit_lines_audit_date ON glide_audit_lines (audit_date);",
]

with engine.connect() as conn:
    conn.execute(text(DDL))
    for stmt in INDEXES:
        conn.execute(text(stmt))
    conn.commit()
    print("audit_reconcile_runs and glide_audit_lines are ready.")
