"""Create payment_chip_matches for best-effort /add ↔ payment notification links.

Usage:
    DATABASE_URL=... python migrate_payment_chip_matches.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS payment_chip_matches (
    id SERIAL PRIMARY KEY,
    payment_method_slug VARCHAR(32) NOT NULL,
    payment_id INTEGER NOT NULL,
    telegram_chat_id BIGINT NOT NULL,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    amount_cents INTEGER NOT NULL,
    via VARCHAR(32) NOT NULL,
    actor_telegram_user_id BIGINT,
    matched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB,
    CONSTRAINT uq_pcm_method_payment UNIQUE (payment_method_slug, payment_id)
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_pcm_telegram_chat_id
    ON payment_chip_matches (telegram_chat_id);
CREATE INDEX IF NOT EXISTS ix_pcm_matched_at
    ON payment_chip_matches (matched_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pcm_crypto_tx_hash
    ON payment_chip_matches ((metadata->>'transaction_hash'))
    WHERE metadata->>'transaction_hash' IS NOT NULL
      AND btrim(metadata->>'transaction_hash') <> '';
"""


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        conn.execute(text(DDL))
        conn.execute(text(INDEXES))
    print("payment_chip_matches table is ready.")


if __name__ == "__main__":
    main()
