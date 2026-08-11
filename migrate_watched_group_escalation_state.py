"""Create watched_group_escalation_state (durable watched non-support group episodes).

Usage:
    DATABASE_URL=... python migrate_watched_group_escalation_state.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
PostgreSQL only (timestamptz / jsonb).
"""

from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS watched_group_escalation_state (
    telegram_chat_id BIGINT PRIMARY KEY,
    title TEXT,
    episode_started_at TIMESTAMPTZ,
    last_message_at TIMESTAMPTZ,
    escalated_at TIMESTAMPTZ,
    burst_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""


def ensure_watched_group_escalation_state(engine=None) -> None:
    """Idempotent: create table if missing."""
    if engine is None:
        engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        conn.commit()


if __name__ == "__main__":
    ensure_watched_group_escalation_state()
    print("watched_group_escalation_state table is ready.")
