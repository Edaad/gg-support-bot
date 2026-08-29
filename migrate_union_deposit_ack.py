"""Add durable union deposit ack-step columns to manual_deposit_requests.

Usage:
    DATABASE_URL=... python migrate_union_deposit_ack.py

Idempotent: safe to run multiple times.
"""

from sqlalchemy import text

from db.connection import init_engine

COLUMN_DDL = [
    """
    ALTER TABLE manual_deposit_requests
    ADD COLUMN IF NOT EXISTS ack_telegram_message_id BIGINT
    """,
    """
    ALTER TABLE manual_deposit_requests
    ADD COLUMN IF NOT EXISTS ack_expires_at TIMESTAMPTZ
    """,
    """
    ALTER TABLE manual_deposit_requests
    ADD COLUMN IF NOT EXISTS ack_expired_at TIMESTAMPTZ
    """,
    """
    ALTER TABLE manual_deposit_requests
    ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ
    """,
    """
    ALTER TABLE manual_deposit_requests
    ADD COLUMN IF NOT EXISTS initiated_by_telegram_user_id BIGINT
    """,
]

INDEX_DDL = """
CREATE INDEX IF NOT EXISTS ix_mdr_ack_expires_pending
ON manual_deposit_requests (ack_expires_at)
WHERE ack_expires_at IS NOT NULL
  AND ack_expired_at IS NULL
  AND acknowledged_at IS NULL
  AND trade_record_checked = false
"""


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        for ddl in COLUMN_DDL:
            conn.execute(text(ddl))
        conn.execute(text(INDEX_DDL))
    print("migrate_union_deposit_ack: done")


if __name__ == "__main__":
    main()
