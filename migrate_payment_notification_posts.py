"""Create payment_notification_posts for fan-out bind-chat notifications.

Usage:
    DATABASE_URL=... python migrate_payment_notification_posts.py

Idempotent: safe to run multiple times (IF NOT EXISTS). Backfills primary
notification ids already stored on payment rows.
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS payment_notification_posts (
    id SERIAL PRIMARY KEY,
    payment_method_slug VARCHAR(32) NOT NULL,
    payment_id INTEGER NOT NULL,
    notification_chat_id BIGINT NOT NULL,
    notification_message_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_pnp_notification_msg UNIQUE (
        notification_chat_id,
        notification_message_id
    )
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_pnp_method_payment
    ON payment_notification_posts (payment_method_slug, payment_id);
CREATE INDEX IF NOT EXISTS ix_pnp_notification_msg
    ON payment_notification_posts (notification_chat_id, notification_message_id);
"""

BACKFILL = """
INSERT INTO payment_notification_posts (
    payment_method_slug,
    payment_id,
    notification_chat_id,
    notification_message_id
)
SELECT 'venmo', id, notification_chat_id, notification_message_id
FROM venmo_payments
WHERE notification_chat_id IS NOT NULL
  AND notification_message_id IS NOT NULL
ON CONFLICT (notification_chat_id, notification_message_id) DO NOTHING;

INSERT INTO payment_notification_posts (
    payment_method_slug,
    payment_id,
    notification_chat_id,
    notification_message_id
)
SELECT 'zelle', id, notification_chat_id, notification_message_id
FROM zelle_payments
WHERE notification_chat_id IS NOT NULL
  AND notification_message_id IS NOT NULL
ON CONFLICT (notification_chat_id, notification_message_id) DO NOTHING;

INSERT INTO payment_notification_posts (
    payment_method_slug,
    payment_id,
    notification_chat_id,
    notification_message_id
)
SELECT 'cashapp', id, notification_chat_id, notification_message_id
FROM cashapp_payments
WHERE notification_chat_id IS NOT NULL
  AND notification_message_id IS NOT NULL
ON CONFLICT (notification_chat_id, notification_message_id) DO NOTHING;

INSERT INTO payment_notification_posts (
    payment_method_slug,
    payment_id,
    notification_chat_id,
    notification_message_id
)
SELECT 'paypal', id, notification_chat_id, notification_message_id
FROM paypal_payments
WHERE notification_chat_id IS NOT NULL
  AND notification_message_id IS NOT NULL
ON CONFLICT (notification_chat_id, notification_message_id) DO NOTHING;

INSERT INTO payment_notification_posts (
    payment_method_slug,
    payment_id,
    notification_chat_id,
    notification_message_id
)
SELECT 'crypto', id, notification_chat_id, notification_message_id
FROM crypto_payments
WHERE notification_chat_id IS NOT NULL
  AND notification_message_id IS NOT NULL
ON CONFLICT (notification_chat_id, notification_message_id) DO NOTHING;
"""


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        conn.execute(text(DDL))
        conn.execute(text(INDEXES))
        conn.execute(text(BACKFILL))
    print("payment_notification_posts table is ready.")


if __name__ == "__main__":
    main()
