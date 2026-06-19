"""Create deploy_notify_state table (Heroku release-phase admin DM cooldown).

Usage:
    DATABASE_URL=... python migrate_deploy_notify_state.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
PostgreSQL only (timestamptz).
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
CREATE TABLE IF NOT EXISTS deploy_notify_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_notified_at TIMESTAMPTZ
);
"""


def ensure_deploy_notify_state(engine=None) -> None:
    """Idempotent: create cooldown table if missing (safe on every release)."""
    if engine is None:
        engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        conn.commit()


if __name__ == "__main__":
    ensure_deploy_notify_state()
    print("deploy_notify_state table is ready.")
