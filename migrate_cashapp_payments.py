"""Create cashapp_payments and cashapp_payer_bindings tables; add cashapp_payment_id to bind attempts.

Usage:
    DATABASE_URL=... python migrate_cashapp_payments.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_PAYMENTS = """
CREATE TABLE IF NOT EXISTS cashapp_payments (
    id SERIAL PRIMARY KEY,
    payer_name VARCHAR(255) NOT NULL,
    amount_cents INTEGER NOT NULL,
    cashapp_handle VARCHAR(100) NOT NULL,
    paid_at VARCHAR(255),
    source_external_id VARCHAR(255) UNIQUE,
    telegram_chat_id BIGINT,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    bound_group_title_at_bind VARCHAR(255),
    notification_chat_id BIGINT,
    notification_message_id BIGINT,
    bound_by_telegram_user_id BIGINT,
    auto_bound BOOLEAN NOT NULL DEFAULT FALSE,
    is_test BOOLEAN NOT NULL DEFAULT FALSE,
    bound_at TIMESTAMPTZ,
    memo TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

DDL_BINDINGS = """
CREATE TABLE IF NOT EXISTS cashapp_payer_bindings (
    id SERIAL PRIMARY KEY,
    payer_name_normalized VARCHAR(255) NOT NULL,
    cashapp_handle VARCHAR(100) NOT NULL,
    telegram_chat_id BIGINT NOT NULL,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    bound_group_title_at_bind VARCHAR(255),
    last_bound_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_bound_by_telegram_user_id BIGINT,
    CONSTRAINT uq_cashapp_payer_bindings_payer_name
        UNIQUE (payer_name_normalized)
);
"""

INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS ix_cashapp_payments_notification_msg
    ON cashapp_payments (notification_chat_id, notification_message_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_cashapp_payments_telegram_chat_id
    ON cashapp_payments (telegram_chat_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_cashapp_payments_created_at
    ON cashapp_payments (created_at);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_cashapp_payer_bindings_telegram_chat_id
    ON cashapp_payer_bindings (telegram_chat_id);
    """,
]

ALTER_COLUMNS = [
    """
    ALTER TABLE payment_method_bind_attempts
    ADD COLUMN IF NOT EXISTS cashapp_payment_id INTEGER
        REFERENCES cashapp_payments(id) ON DELETE SET NULL;
    """,
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL_PAYMENTS))
        conn.execute(text(DDL_BINDINGS))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        for stmt in ALTER_COLUMNS:
            conn.execute(text(stmt))
        conn.commit()
        print(
            "cashapp_payments, cashapp_payer_bindings, and cashapp_payment_id column are ready."
        )
