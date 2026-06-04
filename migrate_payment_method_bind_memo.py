"""Add memo-emoji bind fields to payment_method_bind_attempts and memo on venmo_payments.

Usage:
    DATABASE_URL=... python migrate_payment_method_bind_memo.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

DDL_ATTEMPTS = """
ALTER TABLE payment_method_bind_attempts
    ADD COLUMN IF NOT EXISTS bind_kind VARCHAR(32) NOT NULL DEFAULT 'special_amount';
"""

DDL_ATTEMPTS_EMOJI = """
ALTER TABLE payment_method_bind_attempts
    ADD COLUMN IF NOT EXISTS setup_emoji VARCHAR(32);
"""

DDL_ATTEMPTS_AMOUNT_NULL = """
ALTER TABLE payment_method_bind_attempts
    ALTER COLUMN amount_cents DROP NOT NULL;
"""

DDL_VENMO_MEMO = """
ALTER TABLE venmo_payments
    ADD COLUMN IF NOT EXISTS memo TEXT;
"""

INDEXES = [
    """
    DROP INDEX IF EXISTS uq_pmba_pending_variant_amount;
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_pmba_pending_variant_amount
    ON payment_method_bind_attempts (variant_id, amount_cents)
    WHERE status = 'pending' AND bind_kind = 'special_amount' AND amount_cents IS NOT NULL;
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_pmba_pending_variant_emoji
    ON payment_method_bind_attempts (variant_id, setup_emoji)
    WHERE status = 'pending' AND bind_kind = 'memo_emoji' AND setup_emoji IS NOT NULL;
    """,
]


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        conn.execute(text(DDL_ATTEMPTS))
        conn.execute(text(DDL_ATTEMPTS_EMOJI))
        conn.execute(text(DDL_ATTEMPTS_AMOUNT_NULL))
        conn.execute(text(DDL_VENMO_MEMO))
        for stmt in INDEXES:
            conn.execute(text(stmt.strip()))
    print("payment_method_bind_memo migration complete.")


if __name__ == "__main__":
    main()
