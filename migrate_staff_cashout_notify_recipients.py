"""Staff cashout Pushover notify recipients (name, user key, method prefs).

Usage:
    DATABASE_URL=... python migrate_staff_cashout_notify_recipients.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS staff_cashout_notify_recipients (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    pushover_user_key VARCHAR(64) NOT NULL,
    methods JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        conn.commit()
        print("staff_cashout_notify_recipients: table is ready.")
