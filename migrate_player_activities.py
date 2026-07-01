"""Create player_activities table if missing (deposit/cashout/earlyrb cooldown anchors).

Usage:
    DATABASE_URL=... python migrate_player_activities.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
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
CREATE TABLE IF NOT EXISTS player_activities (
    id SERIAL PRIMARY KEY,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    telegram_user_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    activity_type VARCHAR(10) NOT NULL,
    cancelled BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_player_activities_club_chat_type
    ON player_activities (club_id, chat_id, activity_type, cancelled, created_at DESC);
"""


def ensure_player_activities(engine=None) -> None:
    """Idempotent: create player_activities if missing."""
    if engine is None:
        engine = init_engine()
    with engine.connect() as conn:
        for stmt in DDL.strip().split(";"):
            line = stmt.strip()
            if line:
                conn.execute(text(line))
        conn.commit()


if __name__ == "__main__":
    ensure_player_activities()
    print("player_activities table is ready.")
