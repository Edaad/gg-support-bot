"""Create ``mtproto_session_credentials`` table (shared Telethon StringSession payloads).

Usage:
    DATABASE_URL=... python migrate_mtproto_session_credentials.py

PostgreSQL TIMESTAMPTZ recommended. Safe to rerun (IF NOT EXISTS).

New installs also get the table from SQLAlchemy ``Base.metadata.create_all``.
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS mtproto_session_credentials (
    club_key VARCHAR(64) PRIMARY KEY,
    telethon_auth_string TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        conn.commit()

    print("mtproto_session_credentials table is ready.")
