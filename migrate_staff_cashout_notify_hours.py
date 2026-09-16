"""Global EST cashout notify hours + create_notified_at for deferred alerts.

Usage:
    DATABASE_URL=... python migrate_staff_cashout_notify_hours.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
Backfills create_notified_at so existing Active cashouts are not re-alerted
as "new" when hours open.
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_CONTROL = """
ALTER TABLE staff_cashout_slack_reminder_control
    ADD COLUMN IF NOT EXISTS hours_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE staff_cashout_slack_reminder_control
    ADD COLUMN IF NOT EXISTS hours_start VARCHAR(5) NOT NULL DEFAULT '08:00';
ALTER TABLE staff_cashout_slack_reminder_control
    ADD COLUMN IF NOT EXISTS hours_end VARCHAR(5) NOT NULL DEFAULT '23:00';
"""

DDL_RECORD = """
ALTER TABLE staff_cashout_records
    ADD COLUMN IF NOT EXISTS create_notified_at TIMESTAMP;
"""

BACKFILL = """
UPDATE staff_cashout_records
SET create_notified_at = created_at
WHERE create_notified_at IS NULL
  AND created_at IS NOT NULL;
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        for stmt in DDL_CONTROL.strip().split(";"):
            sql = stmt.strip()
            if sql:
                conn.execute(text(sql))
        conn.execute(text(DDL_RECORD))
        conn.execute(text(BACKFILL))
        conn.commit()
        print(
            "staff_cashout_notify_hours: EST hours columns + "
            "create_notified_at (backfilled) are ready."
        )
