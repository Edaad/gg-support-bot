"""Add chat_id to cooldown_bypasses for per-group bypass (re-grant old user-based bypasses).

Run:
    DATABASE_URL=... python migrate_cooldown_bypass_chat_id.py
"""
from db.connection import init_engine
from sqlalchemy import text

engine = init_engine()
with engine.connect() as conn:
    conn.execute(
        text(
            "ALTER TABLE cooldown_bypasses "
            "ADD COLUMN IF NOT EXISTS chat_id BIGINT"
        )
    )
    conn.execute(
        text(
            "ALTER TABLE cooldown_bypasses "
            "ALTER COLUMN telegram_user_id DROP NOT NULL"
        )
    )
    conn.commit()
    print("cooldown_bypasses.chat_id column is ready.")
