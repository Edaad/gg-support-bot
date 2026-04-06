"""One-time migration: add cashout_max_amount column to clubs table."""
from db.connection import init_engine
from sqlalchemy import text

engine = init_engine()
with engine.connect() as conn:
    conn.execute(text(
        "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS cashout_max_amount NUMERIC(12,2)"
    ))
    conn.commit()
    print("cashout_max_amount column added successfully.")
