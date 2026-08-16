"""Add tracks_money_sent and staff_cashout_money_sends.

Usage:
    DATABASE_URL=... python migrate_staff_cashout_ledger.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

STATEMENTS = [
    """
    ALTER TABLE staff_cashout_records
    ADD COLUMN IF NOT EXISTS tracks_money_sent BOOLEAN NOT NULL DEFAULT FALSE;
    """,
    """
    ALTER TABLE staff_cashout_payments
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();
    """,
    """
    CREATE TABLE IF NOT EXISTS staff_cashout_money_sends (
        id SERIAL PRIMARY KEY,
        cashout_record_id INTEGER NOT NULL
            REFERENCES staff_cashout_records(id) ON DELETE CASCADE,
        sender_name VARCHAR(255) NOT NULL,
        amount NUMERIC(12, 2) NOT NULL,
        payment_method_id INTEGER,
        payment_sub_option_id INTEGER,
        method_display_name VARCHAR(100) NOT NULL,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_staff_cashout_money_sends_record_id
    ON staff_cashout_money_sends (cashout_record_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_staff_cashout_money_sends_created_at
    ON staff_cashout_money_sends (created_at);
    """,
    """
    ALTER TABLE staff_cashout_records
    ALTER COLUMN cashier_job_id DROP NOT NULL;
    """,
    """
    ALTER TABLE staff_cashout_records
    ALTER COLUMN chat_id DROP NOT NULL;
    """,
    """
    ALTER TABLE staff_cashout_records
    ALTER COLUMN recorded_by_telegram_user_id DROP NOT NULL;
    """,
]


if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        for stmt in STATEMENTS:
            conn.execute(text(stmt))
        conn.commit()
        print("staff_cashout_ledger: tracks_money_sent and money_sends are ready.")
