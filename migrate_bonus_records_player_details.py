"""Add player_details linkage columns to bonus_records and bonus_drafts.

Usage:
    DATABASE_URL=... python migrate_bonus_records_player_details.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

RECORDS_ALTER = [
    """
    ALTER TABLE bonus_records
    ADD COLUMN IF NOT EXISTS player_details_id INTEGER
        REFERENCES player_details(id) ON DELETE SET NULL;
    """,
    """
    ALTER TABLE bonus_records
    ADD COLUMN IF NOT EXISTS gg_player_id VARCHAR(255);
    """,
    """
    ALTER TABLE bonus_records
    ADD COLUMN IF NOT EXISTS chat_id BIGINT;
    """,
    """
    ALTER TABLE bonus_records
    ADD COLUMN IF NOT EXISTS group_title VARCHAR(512);
    """,
]

DRAFTS_ALTER = [
    """
    ALTER TABLE bonus_drafts
    ADD COLUMN IF NOT EXISTS gg_player_id VARCHAR(255);
    """,
    """
    ALTER TABLE bonus_drafts
    ADD COLUMN IF NOT EXISTS player_details_id INTEGER
        REFERENCES player_details(id) ON DELETE SET NULL;
    """,
]

INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS ix_bonus_records_gg_player_id
    ON bonus_records (gg_player_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_bonus_records_player_details_id
    ON bonus_records (player_details_id);
    """,
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        for stmt in RECORDS_ALTER + DRAFTS_ALTER:
            conn.execute(text(stmt))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.commit()
        print("bonus_records and bonus_drafts player_details columns are ready.")
