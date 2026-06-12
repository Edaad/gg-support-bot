"""Create deploy_notify_state table (Heroku release-phase admin DM cooldown).

Usage:
    DATABASE_URL=... python migrate_deploy_notify_state.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
PostgreSQL only (timestamptz).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS deploy_notify_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_notified_at TIMESTAMPTZ
);
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        conn.commit()
        print("deploy_notify_state table is ready.")
