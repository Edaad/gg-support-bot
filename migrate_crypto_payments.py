"""Create crypto_payments table.

Usage:
    DATABASE_URL=... python migrate_crypto_payments.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_PAYMENTS = """
CREATE TABLE IF NOT EXISTS crypto_payments (
    id SERIAL PRIMARY KEY,
    amount_cents INTEGER NOT NULL,
    token_symbol VARCHAR(32) NOT NULL,
    token_name VARCHAR(100),
    chain VARCHAR(32) NOT NULL,
    from_address VARCHAR(255) NOT NULL,
    from_entity_name VARCHAR(255),
    to_address VARCHAR(255) NOT NULL,
    transaction_hash VARCHAR(255) NOT NULL,
    paid_at VARCHAR(255),
    source_external_id VARCHAR(255) UNIQUE,
    alert_name VARCHAR(255),
    alert_scope VARCHAR(32) NOT NULL,
    telegram_chat_id BIGINT,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    bound_group_title_at_bind VARCHAR(255),
    notification_chat_id BIGINT,
    notification_message_id BIGINT,
    bound_by_telegram_user_id BIGINT,
    auto_bound BOOLEAN NOT NULL DEFAULT FALSE,
    is_test BOOLEAN NOT NULL DEFAULT FALSE,
    bound_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS ix_crypto_payments_notification_msg
    ON crypto_payments (notification_chat_id, notification_message_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_crypto_payments_telegram_chat_id
    ON crypto_payments (telegram_chat_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_crypto_payments_created_at
    ON crypto_payments (created_at);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_crypto_payments_alert_scope
    ON crypto_payments (alert_scope);
    """,
]

ALTER_COLUMNS = [
    """
    ALTER TABLE crypto_payments
    ADD COLUMN IF NOT EXISTS alert_scope VARCHAR(32);
    """,
]

BACKFILL_ALERT_SCOPE = """
UPDATE crypto_payments
SET alert_scope = CASE
    WHEN lower(trim(alert_name)) = lower('ClubGTO Crypto Payment') THEN 'clubgto'
    WHEN lower(trim(alert_name)) = lower('RT/AT/CC Crypto Payment') THEN 'rt_at_cc'
    ELSE alert_scope
END
WHERE alert_scope IS NULL AND alert_name IS NOT NULL;
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL_PAYMENTS))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        for stmt in ALTER_COLUMNS:
            conn.execute(text(stmt))
        conn.execute(text(BACKFILL_ALERT_SCOPE))
        conn.commit()
        print("crypto_payments table is ready.")
