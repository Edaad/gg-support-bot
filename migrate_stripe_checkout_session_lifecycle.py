"""Add lifecycle columns to stripe_checkout_sessions for webhook updates.

Usage:
    DATABASE_URL=... python migrate_stripe_checkout_session_lifecycle.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

ALTER_STATEMENTS = [
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
        for stmt in ALTER_STATEMENTS:
            conn.execute(text(stmt))
        conn.commit()
        print("stripe_checkout_sessions lifecycle columns are ready.")
