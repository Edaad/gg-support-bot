"""Create bonus_types and bonus_records tables.

Usage:
    DATABASE_URL=... python migrate_bonus_records.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_TYPES = """
CREATE TABLE IF NOT EXISTS bonus_types (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    is_active BOOLEAN DEFAULT TRUE,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);
"""

DDL_RECORDS = """
CREATE TABLE IF NOT EXISTS bonus_records (
    id SERIAL PRIMARY KEY,
    player_username VARCHAR(255) NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    bonus_type_id INTEGER REFERENCES bonus_types(id) ON DELETE SET NULL,
    custom_description TEXT,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    admin_telegram_user_id BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_bonus_records_created_at ON bonus_records (created_at);",
    "CREATE INDEX IF NOT EXISTS ix_bonus_records_club_id ON bonus_records (club_id);",
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL_TYPES))
        conn.execute(text(DDL_RECORDS))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.commit()
        print("bonus_types and bonus_records are ready.")
