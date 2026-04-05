"""One-time migration: create method_variants table.

Run on Heroku:
  heroku run python migrate_variants.py
"""

import os
from sqlalchemy import create_engine, text

url = os.environ["DATABASE_URL"]
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql://", 1)

engine = create_engine(url)
with engine.begin() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS method_variants (
            id SERIAL PRIMARY KEY,
            method_id INTEGER NOT NULL REFERENCES payment_methods(id) ON DELETE CASCADE,
            label VARCHAR(100) NOT NULL,
            weight INTEGER NOT NULL DEFAULT 1,
            response_type VARCHAR(10) DEFAULT 'text',
            response_text TEXT,
            response_file_id TEXT,
            response_caption TEXT,
            sort_order INTEGER DEFAULT 0
        )
    """))
    print("method_variants table created (or already exists).")
