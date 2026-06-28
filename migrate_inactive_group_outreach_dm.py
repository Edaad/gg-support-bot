"""Add DM campaign + re-onboard columns for inactive group outreach.

Usage:
    DATABASE_URL=... python migrate_inactive_group_outreach_dm.py

Idempotent: safe to run multiple times (ADD COLUMN IF NOT EXISTS).
PostgreSQL only (timestamptz).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_CONTROL = """
ALTER TABLE inactive_group_outreach_control
    ADD COLUMN IF NOT EXISTS dm_campaign_message TEXT,
    ADD COLUMN IF NOT EXISTS dm_batch_status VARCHAR(32),
    ADD COLUMN IF NOT EXISTS dm_campaign_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS dm_campaign_started_by_telegram_user_id BIGINT,
    ADD COLUMN IF NOT EXISTS dm_sent_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS dm_failed_count INTEGER NOT NULL DEFAULT 0;
"""

DDL_ROW = """
ALTER TABLE inactive_group_outreach_rows
    ADD COLUMN IF NOT EXISTS reply_received_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS reonboard_new_chat_id BIGINT,
    ADD COLUMN IF NOT EXISTS reonboard_error TEXT,
    ADD COLUMN IF NOT EXISTS old_group_erased_at TIMESTAMPTZ;
"""

DDL_INDEX = """
CREATE INDEX IF NOT EXISTS ix_inactive_group_outreach_rows_dm_lookup
    ON inactive_group_outreach_rows (club_key, player_telegram_user_id, dm_status);
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL_CONTROL))
        conn.execute(text(DDL_ROW))
        conn.execute(text(DDL_INDEX))
        conn.commit()
        print("inactive_group_outreach DM + re-onboard columns are ready.")
