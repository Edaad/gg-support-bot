"""One-time migration: create player_details table (GG player + club + chat_ids bigint[]).

Usage:
    DATABASE_URL=... python migrate_player_details.py

Idempotent: safe to run multiple times (IF NOT EXISTS).

PostgreSQL only (ARRAY, GIN). chat_ids has no FK to groups — array elements cannot reference groups.chat_id in PG.
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

DDL = """
CREATE TABLE IF NOT EXISTS player_details (
    id SERIAL PRIMARY KEY,
    chat_ids BIGINT[] NOT NULL DEFAULT '{}'::bigint[],
    gg_player_id VARCHAR(255) NOT NULL,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    CONSTRAINT uq_player_details_gg_player_club UNIQUE (gg_player_id, club_id)
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_player_details_club_id ON player_details (club_id);",
    "CREATE INDEX IF NOT EXISTS ix_player_details_gg_player_id ON player_details (gg_player_id);",
    "CREATE INDEX IF NOT EXISTS ix_player_details_chat_ids ON player_details USING GIN (chat_ids);",
]

with engine.connect() as conn:
    conn.execute(text(DDL))
    for stmt in INDEXES:
        conn.execute(text(stmt))
    conn.commit()
    print("player_details table and indexes are ready.")
