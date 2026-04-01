"""One-time migration: add cashout cooldown columns to clubs table."""
from db.connection import init_engine
from sqlalchemy import text

engine = init_engine()
with engine.connect() as conn:
    stmts = [
        "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS cashout_cooldown_enabled BOOLEAN DEFAULT FALSE",
        "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS cashout_cooldown_hours INTEGER DEFAULT 24",
        "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS cashout_hours_enabled BOOLEAN DEFAULT FALSE",
        "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS cashout_hours_start VARCHAR(5) DEFAULT '08:00'",
        "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS cashout_hours_end VARCHAR(5) DEFAULT '23:00'",
    ]
    for s in stmts:
        conn.execute(text(s))
    conn.commit()
    print("All cooldown columns added successfully.")
