"""Add durable union instruction expiry columns to manual_deposit_requests.

Usage:
    DATABASE_URL=... python migrate_union_instruction_expiry.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

COLUMN_DDL = [
    """
    ALTER TABLE manual_deposit_requests
    ADD COLUMN IF NOT EXISTS instruction_telegram_message_ids BIGINT[]
    """,
    """
    ALTER TABLE manual_deposit_requests
    ADD COLUMN IF NOT EXISTS instruction_expires_at TIMESTAMPTZ
    """,
    """
    ALTER TABLE manual_deposit_requests
    ADD COLUMN IF NOT EXISTS instruction_expired_at TIMESTAMPTZ
    """,
]

INDEX_DDL = """
CREATE INDEX IF NOT EXISTS ix_mdr_instruction_expires_pending
ON manual_deposit_requests (instruction_expires_at)
WHERE instruction_expires_at IS NOT NULL
  AND instruction_expired_at IS NULL
  AND trade_record_checked = false
"""


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        for ddl in COLUMN_DDL:
            conn.execute(text(ddl))
        conn.execute(text(INDEX_DDL))
    print("migrate_union_instruction_expiry: done")


if __name__ == "__main__":
    main()
