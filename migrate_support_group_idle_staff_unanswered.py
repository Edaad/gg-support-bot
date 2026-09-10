"""Add staff-unanswered timer columns on support_group_idle_episode_state.

Usage:
    DATABASE_URL=... python migrate_support_group_idle_staff_unanswered.py

Idempotent: safe to run multiple times (ADD COLUMN IF NOT EXISTS).
PostgreSQL only (timestamptz / text).
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
ALTER TABLE support_group_idle_episode_state
    ADD COLUMN IF NOT EXISTS staff_unanswered_armed_at TIMESTAMPTZ;
ALTER TABLE support_group_idle_episode_state
    ADD COLUMN IF NOT EXISTS staff_unanswered_fired_at TIMESTAMPTZ;
ALTER TABLE support_group_idle_episode_state
    ADD COLUMN IF NOT EXISTS staff_unanswered_message_text TEXT;
"""


def ensure_support_group_idle_staff_unanswered(engine=None) -> None:
    """Idempotent: add staff-unanswered columns if missing."""
    if engine is None:
        engine = init_engine()
    with engine.connect() as conn:
        for stmt in DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(text(s))
        conn.commit()


if __name__ == "__main__":
    ensure_support_group_idle_staff_unanswered()
    print("support_group_idle_episode_state staff-unanswered columns are ready.")
