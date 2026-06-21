"""Create staff_cashout_records and staff_cashout_payments tables.

Usage:
    DATABASE_URL=... python migrate_staff_cashout_records.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_RECORDS = """
CREATE TABLE IF NOT EXISTS staff_cashout_records (
    id SERIAL PRIMARY KEY,
    cashier_job_id INTEGER NOT NULL UNIQUE REFERENCES cashier_cashout_jobs(id) ON DELETE CASCADE,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    chat_id BIGINT NOT NULL,
    group_title VARCHAR(255) NOT NULL,
    gg_player_id VARCHAR(64),
    amount NUMERIC(12, 2) NOT NULL,
    recorded_by_telegram_user_id BIGINT NOT NULL,
    trigger VARCHAR(20) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
"""

DDL_PAYMENTS = """
CREATE TABLE IF NOT EXISTS staff_cashout_payments (
    id SERIAL PRIMARY KEY,
    cashout_record_id INTEGER NOT NULL REFERENCES staff_cashout_records(id) ON DELETE CASCADE,
    payment_method_id INTEGER,
    payment_sub_option_id INTEGER,
    method_display_name VARCHAR(100),
    payout_details TEXT,
    amount NUMERIC(12, 2),
    sort_order INTEGER NOT NULL DEFAULT 0
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_staff_cashout_records_club_id ON staff_cashout_records (club_id);",
    "CREATE INDEX IF NOT EXISTS ix_staff_cashout_records_created_at ON staff_cashout_records (created_at);",
    "CREATE INDEX IF NOT EXISTS ix_staff_cashout_payments_record_id ON staff_cashout_payments (cashout_record_id);",
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL_RECORDS))
        conn.execute(text(DDL_PAYMENTS))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.commit()
        print("staff_cashout_records and staff_cashout_payments are ready.")
