"""Create ``mtproto_club_health`` (worker Telethon status for Dashboard).

Usage:
    DATABASE_URL=... python migrate_mtproto_club_health.py

Safe to rerun (IF NOT EXISTS). New installs also get the table from ``create_all``.
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS mtproto_club_health (
    club_key VARCHAR(64) PRIMARY KEY,
    worker_connected BOOLEAN NOT NULL DEFAULT FALSE,
    session_valid BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(32) NOT NULL DEFAULT 'unknown',
    status_detail TEXT,
    telegram_user_id BIGINT,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        conn.commit()

    print("mtproto_club_health table is ready.")
