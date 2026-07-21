"""One-time migration: durable popup reply keyboard installed flag.

Adds:
    support_group_chats.popup_keyboard_installed — whether player keyboard is up

Usage:
    DATABASE_URL=... python migrate_popup_keyboard_installed.py

Idempotent: safe to run multiple times (ADD COLUMN IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

engine = init_engine()

STATEMENTS = [
    "ALTER TABLE support_group_chats ADD COLUMN IF NOT EXISTS "
    "popup_keyboard_installed BOOLEAN NOT NULL DEFAULT FALSE;",
]

with engine.connect() as conn:
    for stmt in STATEMENTS:
        conn.execute(text(stmt))
    conn.commit()
    print("popup_keyboard_installed column is ready.")
