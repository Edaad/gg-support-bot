"""Create stripe_customers and stripe_checkout_sessions tables.

Usage:
    DATABASE_URL=... python migrate_stripe_deposit_tracking.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_CUSTOMERS = """
CREATE TABLE IF NOT EXISTS stripe_customers (
    id SERIAL PRIMARY KEY,
    telegram_chat_id BIGINT NOT NULL UNIQUE,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    stripe_customer_id VARCHAR(255) NOT NULL UNIQUE,
    gg_player_id VARCHAR(255),
    player_display_name VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

DDL_SESSIONS = """
CREATE TABLE IF NOT EXISTS stripe_checkout_sessions (
    id SERIAL PRIMARY KEY,
    stripe_checkout_session_id VARCHAR(255) NOT NULL UNIQUE,
    stripe_customer_id VARCHAR(255) NOT NULL
        REFERENCES stripe_customers(stripe_customer_id) ON DELETE CASCADE,
    telegram_chat_id BIGINT NOT NULL,
    club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    amount_cents INTEGER NOT NULL,
    currency VARCHAR(10) NOT NULL DEFAULT 'usd',
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    payment_method_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_stripe_customers_club_id ON stripe_customers (club_id);",
    "CREATE INDEX IF NOT EXISTS ix_stripe_customers_stripe_customer_id ON stripe_customers (stripe_customer_id);",
    "CREATE INDEX IF NOT EXISTS ix_stripe_checkout_sessions_telegram_chat_id ON stripe_checkout_sessions (telegram_chat_id);",
    "CREATE INDEX IF NOT EXISTS ix_stripe_checkout_sessions_stripe_customer_id ON stripe_checkout_sessions (stripe_customer_id);",
    "CREATE INDEX IF NOT EXISTS ix_stripe_checkout_sessions_stripe_checkout_session_id ON stripe_checkout_sessions (stripe_checkout_session_id);",
]

# Lifecycle columns for Payments dashboard + webhooks (idempotent).
LIFECYCLE_ALTER = [
    """
    ALTER TABLE stripe_checkout_sessions
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
    """,
    """
    ALTER TABLE stripe_checkout_sessions
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
    """,
    """
    ALTER TABLE stripe_checkout_sessions
    ADD COLUMN IF NOT EXISTS stripe_payment_intent_id VARCHAR(255);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_stripe_checkout_sessions_club_created
    ON stripe_checkout_sessions (club_id, created_at DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_stripe_checkout_sessions_club_status
    ON stripe_checkout_sessions (club_id, status);
    """,
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL_CUSTOMERS))
        conn.execute(text(DDL_SESSIONS))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        for stmt in LIFECYCLE_ALTER:
            conn.execute(text(stmt))
        conn.commit()
        print("stripe_customers and stripe_checkout_sessions are ready (including lifecycle columns).")
