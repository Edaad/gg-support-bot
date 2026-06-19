"""Create payment_binding_events audit table.

Usage:
    DATABASE_URL=... python migrate_payment_binding_events.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS payment_binding_events (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR(32) NOT NULL,
    payment_method_slug VARCHAR(32) NOT NULL,
    payment_id INTEGER,
    bind_attempt_id INTEGER REFERENCES payment_method_bind_attempts(id) ON DELETE SET NULL,
    group_binding_id INTEGER REFERENCES group_payment_method_bindings(id) ON DELETE SET NULL,
    telegram_chat_id BIGINT,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    bound_group_title VARCHAR(255),
    bound_via VARCHAR(32),
    auto_bound BOOLEAN,
    actor_telegram_user_id BIGINT,
    notification_chat_id BIGINT,
    notification_message_id BIGINT,
    previous_telegram_chat_id BIGINT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_pbe_method_payment
    ON payment_binding_events (payment_method_slug, payment_id);
CREATE INDEX IF NOT EXISTS ix_pbe_notification_msg
    ON payment_binding_events (notification_chat_id, notification_message_id);
CREATE INDEX IF NOT EXISTS ix_pbe_event_type_created
    ON payment_binding_events (event_type, created_at);
CREATE INDEX IF NOT EXISTS ix_pbe_telegram_chat_id
    ON payment_binding_events (telegram_chat_id);
"""


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        conn.execute(text(DDL))
        conn.execute(text(INDEXES))
    print("payment_binding_events table is ready.")


if __name__ == "__main__":
    main()
