"""Create payment_auto_deposit_events table for e2e auto-deposit analytics.

Usage:
    DATABASE_URL=... python migrate_payment_auto_deposit_events.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS payment_auto_deposit_events (
    id SERIAL PRIMARY KEY,
    payment_method_slug VARCHAR(32) NOT NULL,
    payment_id INTEGER NOT NULL,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    telegram_chat_id BIGINT,
    amount_cents INTEGER NOT NULL,
    auto_bound BOOLEAN NOT NULL DEFAULT FALSE,
    goods_or_services BOOLEAN NOT NULL DEFAULT FALSE,
    group_title VARCHAR(255),
    gg_player_id VARCHAR(64),
    club_auto_deposit_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(32) NOT NULL,
    skip_reason VARCHAR(64),
    chip_add_status VARCHAR(32),
    payment_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_pade_method_payment UNIQUE (payment_method_slug, payment_id)
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_pade_club_payment_at
    ON payment_auto_deposit_events (club_id, payment_at);
CREATE INDEX IF NOT EXISTS ix_pade_method_payment_at
    ON payment_auto_deposit_events (payment_method_slug, payment_at);
CREATE INDEX IF NOT EXISTS ix_pade_status
    ON payment_auto_deposit_events (status);
CREATE INDEX IF NOT EXISTS ix_pade_telegram_chat_id
    ON payment_auto_deposit_events (telegram_chat_id);
"""


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        conn.execute(text(DDL))
        conn.execute(text(INDEXES))
    print("payment_auto_deposit_events table is ready.")


if __name__ == "__main__":
    main()
