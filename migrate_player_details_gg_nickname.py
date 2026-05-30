"""Add player_details.gg_nickname (in-game name from gg-computer).

Usage:
    DATABASE_URL=... python migrate_player_details_gg_nickname.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

DDL = """
ALTER TABLE player_details
ADD COLUMN IF NOT EXISTS gg_nickname VARCHAR(255);
"""

with engine.connect() as conn:
    conn.execute(text(DDL))
    conn.commit()
    print("player_details.gg_nickname column is ready.")
