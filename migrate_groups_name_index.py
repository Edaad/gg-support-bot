"""One-time migration: index groups (club_id, name) for title lookup.

Usage:
    DATABASE_URL=... python migrate_groups_name_index.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_groups_club_id_name ON groups (club_id, name);"
)

with engine.connect() as conn:
    conn.execute(text(INDEX))
    conn.commit()
    print("groups (club_id, name) index is ready.")
