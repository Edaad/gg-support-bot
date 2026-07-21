"""One-time migration: add popup reply keyboard toggle per club.

Adds:
    clubs.enable_popup_keyboard — player Deposit/Cashout/Other reply keyboard

Usage:
    DATABASE_URL=... python migrate_enable_popup_keyboard.py

Idempotent: safe to run multiple times (ADD COLUMN IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

STATEMENTS = [
    "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS enable_popup_keyboard "
    "BOOLEAN NOT NULL DEFAULT FALSE;",
]

with engine.connect() as conn:
    for stmt in STATEMENTS:
        conn.execute(text(stmt))
    conn.commit()
    print("enable_popup_keyboard column is ready.")
