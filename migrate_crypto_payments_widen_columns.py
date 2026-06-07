"""Widen crypto_payments address and transaction_hash columns.

Some chains (non-EVM, indexed tx ids) exceed VARCHAR(66) and caused 500s on ingest.

Usage:
    DATABASE_URL=... python migrate_crypto_payments_widen_columns.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

ALTERS = [
    "ALTER TABLE crypto_payments ALTER COLUMN from_address TYPE VARCHAR(255);",
    "ALTER TABLE crypto_payments ALTER COLUMN to_address TYPE VARCHAR(255);",
    "ALTER TABLE crypto_payments ALTER COLUMN transaction_hash TYPE VARCHAR(255);",
]

if __name__ == "__main__":
    engine = init_engine()
    with engine.connect() as conn:
        for stmt in ALTERS:
            conn.execute(text(stmt))
        conn.commit()
        print("crypto_payments address/tx columns widened to VARCHAR(255).")
