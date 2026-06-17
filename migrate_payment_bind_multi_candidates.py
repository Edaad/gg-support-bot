"""Allow multiple group-chat candidates per payer/wallet identity.

Replaces single-row unique constraints with composite (identity, telegram_chat_id).

Usage:
    DATABASE_URL=... python migrate_payment_bind_multi_candidates.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

_PAYER_TABLES = (
    "venmo_payer_bindings",
    "zelle_payer_bindings",
    "cashapp_payer_bindings",
    "paypal_payer_bindings",
)

_DROP_PAYER_UQ = """
ALTER TABLE {table}
    DROP CONSTRAINT IF EXISTS uq_{table}_payer_name;
"""

_CREATE_PAYER_UQ = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_payer_chat
    ON {table} (payer_name_normalized, telegram_chat_id);
"""

_DROP_CRYPTO_UQ = """
ALTER TABLE crypto_wallet_bindings
    DROP CONSTRAINT IF EXISTS uq_crypto_wallet_bindings_address_scope;
"""

_CREATE_CRYPTO_UQ = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_crypto_wallet_bindings_address_scope_chat
    ON crypto_wallet_bindings (from_address_normalized, alert_scope, telegram_chat_id);
"""


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        for table in _PAYER_TABLES:
            conn.execute(text(_DROP_PAYER_UQ.format(table=table)))
            conn.execute(text(_CREATE_PAYER_UQ.format(table=table)))
        conn.execute(text(_DROP_CRYPTO_UQ))
        conn.execute(text(_CREATE_CRYPTO_UQ))
    print("payment_bind_multi_candidates migration complete.")


if __name__ == "__main__":
    main()
