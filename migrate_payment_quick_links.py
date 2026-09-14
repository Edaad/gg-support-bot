"""Admin-configured quick-access links on the Payments dashboard.

Usage:
    DATABASE_URL=... python migrate_payment_quick_links.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS payment_quick_links (
    id SERIAL PRIMARY KEY,
    title VARCHAR(120) NOT NULL,
    url TEXT NOT NULL,
    method VARCHAR(32),
    club_id INTEGER REFERENCES clubs(id) ON DELETE CASCADE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        conn.commit()
        print("payment_quick_links: table is ready.")
