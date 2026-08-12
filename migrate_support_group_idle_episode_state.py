"""Create support_group_idle_episode_state (durable support-group idle episodes).

Usage:
    DATABASE_URL=... python migrate_support_group_idle_episode_state.py

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
CREATE TABLE IF NOT EXISTS support_group_idle_episode_state (
    telegram_chat_id BIGINT PRIMARY KEY,
    title TEXT,
    episode_started_at TIMESTAMPTZ,
    last_human_at TIMESTAMPTZ,
    burst_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""


def ensure_support_group_idle_episode_state(engine=None) -> None:
    """Idempotent: create table if missing."""
    if engine is None:
        engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        conn.commit()


if __name__ == "__main__":
    ensure_support_group_idle_episode_state()
    print("support_group_idle_episode_state table is ready.")
