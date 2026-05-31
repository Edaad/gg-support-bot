"""Create venmo_payments and venmo_payer_bindings tables.

Usage:
    DATABASE_URL=... python migrate_venmo_payments.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_PAYMENTS = """
CREATE TABLE IF NOT EXISTS venmo_payments (
    id SERIAL PRIMARY KEY,
    payer_name VARCHAR(255) NOT NULL,
    amount_cents INTEGER NOT NULL,
    venmo_handle VARCHAR(100) NOT NULL,
    goods_or_services BOOLEAN NOT NULL DEFAULT FALSE,
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

DDL_BINDINGS = """
CREATE TABLE IF NOT EXISTS venmo_payer_bindings (
    id SERIAL PRIMARY KEY,
    payer_name_normalized VARCHAR(255) NOT NULL,
    venmo_handle VARCHAR(100) NOT NULL,
    telegram_chat_id BIGINT NOT NULL,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    bound_group_title_at_bind VARCHAR(255),
    last_bound_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_bound_by_telegram_user_id BIGINT,
    CONSTRAINT uq_venmo_payer_bindings_payer_handle
        UNIQUE (payer_name_normalized, venmo_handle)
);
"""

INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS ix_venmo_payments_notification_msg
    ON venmo_payments (notification_chat_id, notification_message_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_venmo_payments_telegram_chat_id
    ON venmo_payments (telegram_chat_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_venmo_payments_created_at
    ON venmo_payments (created_at);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_venmo_payer_bindings_telegram_chat_id
    ON venmo_payer_bindings (telegram_chat_id);
    """,
]

ALTER_COLUMNS = [
    """
    ALTER TABLE venmo_payments
    ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE;
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
        print("venmo_payments and venmo_payer_bindings are ready.")
