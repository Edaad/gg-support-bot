"""Add last_slack_summary_at to migration_recovery_control (DB-backed 6h Slack cadence).

Usage:
    DATABASE_URL=... python migrate_migration_recovery_slack_summary_last.py

Idempotent: safe to run multiple times (ADD COLUMN IF NOT EXISTS).
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
ALTER TABLE migration_recovery_control
    ADD COLUMN IF NOT EXISTS last_slack_summary_at TIMESTAMPTZ;
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        conn.commit()
        print("migration_recovery_control.last_slack_summary_at is ready.")
