"""Create bot_flow_sessions table and deposit_session_id FK columns.

Usage:
    DATABASE_URL=... python migrate_bot_flow_sessions.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS bot_flow_sessions (
    session_uuid VARCHAR(64) PRIMARY KEY,
    telegram_chat_id BIGINT NOT NULL,
    flow_type VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    telegram_user_id BIGINT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    end_reason VARCHAR(32)
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_bfs_chat_status
    ON bot_flow_sessions (telegram_chat_id, status);
CREATE INDEX IF NOT EXISTS ix_bfs_flow_type_status
    ON bot_flow_sessions (flow_type, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_bfs_active_chat
    ON bot_flow_sessions (telegram_chat_id)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS ix_pmba_deposit_session_id
    ON payment_method_bind_attempts (deposit_session_id)
    WHERE deposit_session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_stripe_checkout_deposit_session_id
    ON stripe_checkout_sessions (deposit_session_id)
    WHERE deposit_session_id IS NOT NULL;
"""

ALTER_STATEMENTS = [
    """
    ALTER TABLE payment_method_bind_attempts
    ADD COLUMN IF NOT EXISTS deposit_session_id VARCHAR(64);
    """,
    """
    ALTER TABLE stripe_checkout_sessions
    ADD COLUMN IF NOT EXISTS deposit_session_id VARCHAR(64);
    """,
]


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        conn.execute(text(DDL))
        for stmt in ALTER_STATEMENTS:
            conn.execute(text(stmt))
        conn.execute(text(INDEXES))
    print("bot_flow_sessions table and deposit_session_id columns are ready.")


if __name__ == "__main__":
    main()
