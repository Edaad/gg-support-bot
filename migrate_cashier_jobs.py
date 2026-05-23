"""One-time migration: create cashier_cashout_jobs table for GGCashier.

Usage:
    DATABASE_URL=... python migrate_cashier_jobs.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

DDL = """
CREATE TABLE IF NOT EXISTS cashier_cashout_jobs (
    id SERIAL PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    chat_id BIGINT NOT NULL,
    group_title VARCHAR(255) NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    payment_method_id INTEGER REFERENCES payment_methods(id) ON DELETE SET NULL,
    payment_sub_option_id INTEGER REFERENCES payment_sub_options(id) ON DELETE SET NULL,
    method_display_name VARCHAR(100),
    payout_details TEXT,
    trade_record_checked BOOLEAN DEFAULT FALSE,
    cooldown_checked BOOLEAN DEFAULT FALSE,
    initiated_by BIGINT NOT NULL,
    trigger VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'initiated',
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_cashier_cashout_jobs_status ON cashier_cashout_jobs (status);",
    "CREATE INDEX IF NOT EXISTS ix_cashier_cashout_jobs_initiated_by ON cashier_cashout_jobs (initiated_by);",
    "CREATE INDEX IF NOT EXISTS ix_cashier_cashout_jobs_chat_id ON cashier_cashout_jobs (chat_id);",
]

with engine.connect() as conn:
    conn.execute(text(DDL))
    for stmt in INDEXES:
        conn.execute(text(stmt))
    conn.commit()
    print("cashier_cashout_jobs table and indexes are ready.")
