"""Add manual staging columns to inactive group outreach rows.

Usage:
    DATABASE_URL=... python migrate_inactive_group_outreach_staging.py

Idempotent: safe to run multiple times (ADD COLUMN IF NOT EXISTS).
PostgreSQL only (timestamptz).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_COLUMNS = """
ALTER TABLE inactive_group_outreach_rows
    ADD COLUMN IF NOT EXISTS stage_status VARCHAR(32),
    ADD COLUMN IF NOT EXISTS staged_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS staged_by_telegram_user_id BIGINT,
    ADD COLUMN IF NOT EXISTS stage_note TEXT;
"""

DDL_INDEX = """
CREATE INDEX IF NOT EXISTS ix_inactive_group_outreach_rows_stage_status
    ON inactive_group_outreach_rows (stage_status, club_key);
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL_COLUMNS))
        conn.execute(text(DDL_INDEX))
        conn.commit()
        print("inactive_group_outreach staging columns are ready.")
