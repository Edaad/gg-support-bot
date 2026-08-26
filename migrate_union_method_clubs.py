"""Add club_payment_method_clubs junction + unique slug among union methods.

Usage:
    DATABASE_URL=... python migrate_union_method_clubs.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS club_payment_method_clubs (
    id SERIAL PRIMARY KEY,
    method_id INTEGER NOT NULL REFERENCES club_payment_methods(id) ON DELETE CASCADE,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_cpmc_method_club UNIQUE (method_id, club_id)
)
"""

INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS ix_cpmc_club_id
    ON club_payment_method_clubs (club_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_cpmc_method_id
    ON club_payment_method_clubs (method_id)
    """,
]

BACKFILL = """
INSERT INTO club_payment_method_clubs (method_id, club_id)
SELECT id, club_id
FROM club_payment_methods
WHERE tracks_manual_requests = true
ON CONFLICT (method_id, club_id) DO NOTHING
"""

# One slug among all union deposit methods (shared multi-club configs).
PARTIAL_UNIQUE_SLUG = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_cpm_union_deposit_slug
ON club_payment_methods (slug)
WHERE tracks_manual_requests = true AND direction = 'deposit'
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(TABLE_DDL))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.execute(text(BACKFILL))
        conn.execute(text(PARTIAL_UNIQUE_SLUG))
        conn.commit()
        print("club_payment_method_clubs is ready.")
