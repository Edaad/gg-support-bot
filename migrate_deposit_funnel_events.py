"""Create deposit_funnel_events table for /deposit → chips funnel analytics.

Usage:
    DATABASE_URL=... python migrate_deposit_funnel_events.py

Idempotent: safe to run multiple times (IF NOT EXISTS).
"""

from sqlalchemy import text

from db.connection import init_engine

DDL = """
CREATE TABLE IF NOT EXISTS deposit_funnel_events (
    id SERIAL PRIMARY KEY,
    deposit_session_id VARCHAR(64) NOT NULL,
    step VARCHAR(64) NOT NULL,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    telegram_user_id BIGINT,
    telegram_chat_id BIGINT NOT NULL,
    method_slug VARCHAR(32),
    amount_cents INTEGER,
    is_first_deposit BOOLEAN NOT NULL DEFAULT FALSE,
    requires_method_setup BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_dfe_session_step UNIQUE (deposit_session_id, step)
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_dfe_club_created_at
    ON deposit_funnel_events (club_id, created_at);
CREATE INDEX IF NOT EXISTS ix_dfe_chat_created_at
    ON deposit_funnel_events (telegram_chat_id, created_at);
CREATE INDEX IF NOT EXISTS ix_dfe_step_created_at
    ON deposit_funnel_events (step, created_at);
CREATE INDEX IF NOT EXISTS ix_dfe_session_id
    ON deposit_funnel_events (deposit_session_id);
"""


def main() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        conn.execute(text(DDL))
        conn.execute(text(INDEXES))
    print("deposit_funnel_events table is ready.")


if __name__ == "__main__":
    main()
