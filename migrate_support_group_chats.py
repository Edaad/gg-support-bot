"""Create support_group_chats table (MTProto /gc audit log).

Usage:
    DATABASE_URL=... python migrate_support_group_chats.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
PostgreSQL only (JSONB + timestamptz).

After this script, new installs also get the table from SQLAlchemy ``Base.metadata.create_all``.
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS support_group_chats (
    id SERIAL PRIMARY KEY,
    club_key VARCHAR(64) NOT NULL,
    club_display_name VARCHAR(255) NOT NULL,
    telegram_chat_id BIGINT NOT NULL,
    telegram_chat_title TEXT NOT NULL,
    invite_link TEXT,
    created_by_telegram_user_id BIGINT,
    mtproto_session_name TEXT,
    added_users JSONB,
    failed_users JSONB,
    group_photo_path TEXT,
    initial_group_message_sent BOOLEAN NOT NULL DEFAULT false,
    last_error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_support_group_chats_club_key ON support_group_chats (club_key);",
    "CREATE INDEX IF NOT EXISTS ix_support_group_chats_telegram_chat_id ON support_group_chats (telegram_chat_id);",
    "CREATE INDEX IF NOT EXISTS ix_support_group_chats_created_by ON support_group_chats (created_by_telegram_user_id);",
    "CREATE INDEX IF NOT EXISTS ix_support_group_chats_created_at ON support_group_chats (created_at);",
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.commit()
        print("support_group_chats table and indexes are ready.")
