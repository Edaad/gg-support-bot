"""Create deposit_incomplete_watches for DB-durable T+10m incomplete-deposit escalation.

Usage:
    DATABASE_URL=... python migrate_deposit_incomplete_watches.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS deposit_incomplete_watches (
    telegram_chat_id BIGINT PRIMARY KEY,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    customer_telegram_user_id BIGINT,
    group_title TEXT,
    armed_at TIMESTAMPTZ NOT NULL
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_deposit_incomplete_watches_armed_at
    ON deposit_incomplete_watches (armed_at);
"""


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        conn.execute(text(DDL))
        conn.execute(text(INDEXES))
    print("deposit_incomplete_watches table is ready.")


if __name__ == "__main__":
    main()
