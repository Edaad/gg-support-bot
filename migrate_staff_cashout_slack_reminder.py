"""Staff cashout 5-minute Slack reminder control + per-record last ping.

Usage:
    DATABASE_URL=... python migrate_staff_cashout_slack_reminder.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
PostgreSQL preferred (timestamptz on control row).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_CONTROL = """
CREATE TABLE IF NOT EXISTS staff_cashout_slack_reminder_control (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    enabled_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

SEED = """
INSERT INTO staff_cashout_slack_reminder_control (id)
VALUES (1)
ON CONFLICT (id) DO NOTHING;
"""

DDL_COLUMN = """
ALTER TABLE staff_cashout_records
ADD COLUMN IF NOT EXISTS last_slack_reminder_at TIMESTAMP;
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL_CONTROL))
        conn.execute(text(SEED))
        conn.execute(text(DDL_COLUMN))
        conn.commit()
        print(
            "staff_cashout_slack_reminder: control table + "
            "last_slack_reminder_at column are ready."
        )
