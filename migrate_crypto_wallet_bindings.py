"""Create crypto_wallet_bindings table and backfill from bound crypto payments.

Usage:
    DATABASE_URL=... python migrate_crypto_wallet_bindings.py

Idempotent: safe to run multiple times (IF NOT EXISTS). Backfill uses ON CONFLICT DO NOTHING.
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_BINDINGS = """
CREATE TABLE IF NOT EXISTS crypto_wallet_bindings (
    id SERIAL PRIMARY KEY,
    from_address_normalized VARCHAR(255) NOT NULL,
    alert_scope VARCHAR(32) NOT NULL,
    telegram_chat_id BIGINT NOT NULL,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    bound_group_title_at_bind VARCHAR(255),
    last_bound_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_bound_by_telegram_user_id BIGINT,
    CONSTRAINT uq_crypto_wallet_bindings_address_scope
        UNIQUE (from_address_normalized, alert_scope)
);
"""

INDEXES = [
    """
    CREATE INDEX IF NOT EXISTS ix_crypto_wallet_bindings_telegram_chat_id
    ON crypto_wallet_bindings (telegram_chat_id);
    """,
]

BACKFILL_BINDINGS = """
INSERT INTO crypto_wallet_bindings (
    from_address_normalized,
    alert_scope,
    telegram_chat_id,
    club_id,
    bound_group_title_at_bind,
    last_bound_at,
    last_bound_by_telegram_user_id
)
SELECT DISTINCT ON (lower(trim(from_address)), alert_scope)
    lower(trim(from_address)),
    alert_scope,
    telegram_chat_id,
    club_id,
    bound_group_title_at_bind,
    COALESCE(bound_at, created_at),
    bound_by_telegram_user_id
FROM crypto_payments
WHERE telegram_chat_id IS NOT NULL
  AND trim(from_address) <> ''
ORDER BY lower(trim(from_address)), alert_scope, bound_at DESC NULLS LAST, id DESC
ON CONFLICT (from_address_normalized, alert_scope) DO NOTHING;
"""

COUNT_BINDINGS = "SELECT COUNT(*) FROM crypto_wallet_bindings"
COUNT_SOURCE_WALLETS = """
SELECT COUNT(*) FROM (
    SELECT DISTINCT lower(trim(from_address)), alert_scope
    FROM crypto_payments
    WHERE telegram_chat_id IS NOT NULL
      AND trim(from_address) <> ''
) AS wallets
"""

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        conn.execute(text(DDL_BINDINGS))
        for stmt in INDEXES:
            conn.execute(text(stmt))
        before = conn.execute(text(COUNT_BINDINGS)).scalar_one()
        source_wallets = conn.execute(text(COUNT_SOURCE_WALLETS)).scalar_one()
        result = conn.execute(text(BACKFILL_BINDINGS))
        inserted = result.rowcount
        after = conn.execute(text(COUNT_BINDINGS)).scalar_one()
        conn.commit()
        print("crypto_wallet_bindings table is ready.")
        print(f"  distinct bound wallets in crypto_payments: {source_wallets}")
        print(f"  bindings before backfill: {before}")
        print(f"  bindings inserted (skipped existing): {inserted}")
        print(f"  bindings after backfill: {after}")
