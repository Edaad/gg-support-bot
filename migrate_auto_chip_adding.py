"""One-time migration: add auto chip-adding columns.

Adds:
    clubs.auto_chip_adding_enabled  — per-club toggle for /add → ClubGG deposit bot
    groups.last_deposit_union       — last customer-chosen RT/AT union ("RT"|"AT")
    groups.last_deposit_union_at    — when that union was last recorded

Usage:
    DATABASE_URL=... python migrate_auto_chip_adding.py

Idempotent: safe to run multiple times (ADD COLUMN IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

STATEMENTS = [
    "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS auto_chip_adding_enabled "
    "BOOLEAN NOT NULL DEFAULT FALSE;",
    "ALTER TABLE groups ADD COLUMN IF NOT EXISTS last_deposit_union VARCHAR(2);",
    "ALTER TABLE groups ADD COLUMN IF NOT EXISTS last_deposit_union_at TIMESTAMPTZ;",
]

with engine.connect() as conn:
    for stmt in STATEMENTS:
        conn.execute(text(stmt))
    conn.commit()
    print("auto chip-adding columns are ready.")
