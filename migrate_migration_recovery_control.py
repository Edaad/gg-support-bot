"""Create migration_recovery_control singleton table (auto-disable flag).

Usage:
    DATABASE_URL=... python migrate_migration_recovery_control.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
PostgreSQL only (JSONB + timestamptz).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS migration_recovery_control (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    auto_disabled_at TIMESTAMPTZ,
    auto_disabled_reason TEXT,
    exhausted_club_key VARCHAR(64),
    pending_snapshot JSONB
);
"""

SEED = """
INSERT INTO migration_recovery_control (id)
VALUES (1)
ON CONFLICT (id) DO NOTHING;
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        conn.execute(text(SEED))
        conn.commit()
        print("migration_recovery_control table is ready.")
