"""Add club_rate_limit_resume_at to migration_recovery_control (per-club FloodWait).

Usage:
    DATABASE_URL=... python migrate_migration_recovery_club_rate_limit.py

Idempotent: safe to run multiple times (ADD COLUMN IF NOT EXISTS).
PostgreSQL only (jsonb).
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
ALTER TABLE migration_recovery_control
    ADD COLUMN IF NOT EXISTS club_rate_limit_resume_at JSONB;
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        conn.commit()
        print("migration_recovery_control.club_rate_limit_resume_at is ready.")
