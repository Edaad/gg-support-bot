"""Create deposit_method_alerts table for the admin Alerts dashboard.

Usage:
    DATABASE_URL=... python migrate_deposit_method_alerts.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS deposit_method_alerts (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    method VARCHAR(32) NOT NULL,
    variant VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    conditions JSONB NOT NULL DEFAULT '[]',
    last_fired_week_id VARCHAR(10),
    last_fired_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT ck_dma_method CHECK (
        method IN ('venmo', 'zelle', 'cashapp', 'paypal', 'crypto')
    )
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_dma_method_variant "
    "ON deposit_method_alerts (method, variant);",
    "CREATE INDEX IF NOT EXISTS ix_dma_is_active ON deposit_method_alerts (is_active);",
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        conn.commit()
        print("deposit_method_alerts table is ready.")
